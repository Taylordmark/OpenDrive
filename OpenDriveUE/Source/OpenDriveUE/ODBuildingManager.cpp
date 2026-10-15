#include "ODBuildingManager.h"
#include "ODVisualStyle.h"

#include "ODBinSidecarUtil.h"
#include "ODTileSource.h"
#include "ODRoadManager.h"
#include "ODTerrainManager.h"
#include "ODVehiclePawn.h"
#include "Algo/Reverse.h"
#include "Async/Async.h"
#include "Components/DynamicMeshComponent.h"
#include "DynamicMesh/DynamicMesh3.h"
#include "DynamicMesh/DynamicMeshAttributeSet.h"
#include "DynamicMesh/DynamicMeshOverlay.h"
#include "DynamicMesh/MeshNormals.h"
#include "EngineUtils.h"
#include "Kismet/GameplayStatics.h"
#include "Materials/MaterialInterface.h"
#include "Misc/Crc.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"

using namespace UE::Geometry;

namespace
{
	// Combine two 32-bit hash values (Boost-style hash_combine) - used below
	// to build a handful of decorrelated per-building pseudo-random channels
	// from the building's own baked footprint, with no new pipeline/sidecar
	// data needed (see BuildingVertexColorForFacade()'s own comment for why
	// this lives here instead of a shared header - it's a one-file need).
	uint32 HashCombine32(uint32 A, uint32 B)
	{
		return A ^ (B + 0x9e3779b9u + (A << 6) + (A >> 2));
	}

	float HashToUnitFloat(uint32 H)
	{
		return (float)(H % 1000000u) / 1000000.0f;
	}

	/** Per-building facade-material selectors, packed into vertex color and
	 *  read back by M_BuildingFacade (unreal/apply_building_material.py) -
	 *  the material-variation channel this streaming architecture can
	 *  actually afford: buildings are ONE UDynamicMeshComponent EACH (not
	 *  HISM-instanced, see FBuildingTile's header comment), so there's no
	 *  per-instance custom data to write into, and creating ~1800 separate
	 *  MIDs (one per building, live in the field) would multiply that
	 *  already-heavy per-building draw-call count by real per-MID overhead
	 *  for nothing - a single shared material reading a per-vertex value is
	 *  the same "avoid per-building MIDs" convention AODTerrainManager's own
	 *  vertex-color biome tint already established, just carrying different
	 *  payload channels:
	 *    R - wall texture set, baked per building from OSM type + height
	 *        (build_building_mesh.py pick_facade(), ODU2 WallSet byte),
	 *        encoded as (WallSet + 0.5) / NumWallSets so M_BuildingFacade's
	 *        floor(R*N) lands mid-bucket - a HARD per-building pick, not a
	 *        blend, so one building always reads as one material)
	 *    G - window-lit-fraction seed offset (decorrelates which windows
	 *        read as lit between buildings that would otherwise share the
	 *        same per-window hash pattern from a coincidentally similar
	 *        facade UV phase)
	 *    B - roof type: 1 = concrete flat roof, 0 = shingle (ODU2 FlatRoof
	 *        byte; the material lerps its roof set on this)
	 *  G is hashed from the footprint's own baked centroid (deterministic,
	 *  unique per building's location) rather than any RNG, so
	 *  re-finalizing the same building (e.g. after a pending-retry) always
	 *  yields the exact same seed. */
	FVector4f BuildingVertexColorForFacade(const FParsedBuilding& B)
	{
		const uint32 VCount = B.Verts.Num() / 3;
		double CentroidX = 0.0, CentroidY = 0.0;
		for (uint32 v = 0; v < VCount; ++v)
		{
			CentroidX += B.Verts[v * 3 + 0];
			CentroidY += B.Verts[v * 3 + 1];
		}
		if (VCount > 0)
		{
			CentroidX /= VCount;
			CentroidY /= VCount;
		}

		const uint32 Hx = FCrc::MemCrc32(&CentroidX, sizeof(CentroidX));
		const uint32 Hy = FCrc::MemCrc32(&CentroidY, sizeof(CentroidY));
		const uint32 H2 = HashCombine32(Hy, HashCombine32(Hx, 0x2545F491u));

		// MUST match len(WALL_LAYERS) in unreal/apply_building_material*.py
		// (pipeline/tests/test_building_facades.py checks).
		constexpr uint8 NumWallSets = 7;
		const float R = (FMath::Min<uint8>(B.WallSet, NumWallSets - 1) + 0.5f) / NumWallSets;
		return FVector4f(R, HashToUnitFloat(H2), B.FlatRoof ? 1.0f : 0.0f, 1.0f);
	}

	// ---- Realistic pitched roofs (PLAN-VISUALS Track G) --------------------
	// C++ twin of pipeline/building_roofs.py pitch_roof(): same steps, same
	// constants, same hash (see that file for the why of each step);
	// pipeline/tests/test_building_roofs.py checks the geometry - the faces
	// tile the footprint, roof + end walls are watertight - on shapes and on
	// every real tile, and ProfileRoofTile() compares the counts. Runs on
	// the tile-parse worker thread, never on the game thread.
	namespace Roof
	{
		constexpr double EpsZ = 1.0, EpsDup = 1.0, CollinearSin = 0.02, EpsSnap = 0.01, EpsWeld = 1e-3;
		constexpr double MaxAreaM2 = 400.0, MinFill = 0.85, MinSpanM = 3.0, MaxSpanM = 16.0, MaxLengthM = 40.0;
		constexpr double HipFraction = 0.45, PitchMinDeg = 25.0, PitchMaxDeg = 35.0, FacadeTileCm = 400.0;

		double Cross(double Ax, double Ay, double Bx, double By) { return Ax * By - Ay * Bx; }

		double Dist(double Ax, double Ay, double Bx, double By)
		{
			return FMath::Sqrt((Ax - Bx) * (Ax - Bx) + (Ay - By) * (Ay - By));
		}

		uint32 Hash(int32 Ix, int32 Iy)
		{
			uint32 H = ((uint32)Ix * 73856093u) ^ ((uint32)Iy * 19349663u);
			H ^= H >> 16; H *= 0x7FEB352Du;
			H ^= H >> 15; H *= 0x846CA68Bu;
			H ^= H >> 16;
			return H;
		}

		// the line an edge lies on: ring vertices I < J, or a crease A*l + B*s = C
		struct FLine
		{
			bool bCrease = false;
			int32 I = 0, J = 0;
			double A = 0.0, B = 0.0, C = 0.0;
			bool operator<(const FLine& O) const
			{
				if (bCrease != O.bCrease) return bCrease;
				if (bCrease) return A != O.A ? A < O.A : B != O.B ? B < O.B : C < O.C;
				return I != O.I ? I < O.I : J < O.J;
			}
		};

		// a piece corner in OBB-local (l, s); Line = the edge arriving at it
		struct FPt
		{
			double L, S;
			FLine Line;
		};

		bool FootprintRing(const FParsedBuilding& B, double Top, TArray<FVector2d>& Ring, TArray<bool>& IsCap)
		{
			const int32 TCount = B.Tris.Num() / 3;
			auto AtTop = [&](uint32 V) { return FMath::Abs((double)B.Verts[V * 3 + 2] - Top) < EpsZ; };
			auto Key = [](uint32 A, uint32 Bv) { return ((uint64)FMath::Min(A, Bv) << 32) | FMath::Max(A, Bv); };
			IsCap.Init(false, TCount);
			TMap<uint64, int32> Count;
			int32 NumCap = 0;
			for (int32 t = 0; t < TCount; ++t)
			{
				const uint32* T = &B.Tris[t * 3];
				if (AtTop(T[0]) && AtTop(T[1]) && AtTop(T[2]))
				{
					IsCap[t] = true;
					++NumCap;
					for (int32 k = 0; k < 3; ++k) ++Count.FindOrAdd(Key(T[k], T[(k + 1) % 3]));
				}
			}
			if (NumCap == 0) return false;
			TMap<uint32, uint32> Next;
			for (int32 t = 0; t < TCount; ++t)
			{
				if (!IsCap[t]) continue;
				const uint32* T = &B.Tris[t * 3];
				for (int32 k = 0; k < 3; ++k)
				{
					if (Count[Key(T[k], T[(k + 1) % 3])] != 1) continue;
					if (Next.Contains(T[k])) return false;
					Next.Add(T[k], T[(k + 1) % 3]);
				}
			}
			if (Next.Num() < 3) return false;
			uint32 Start = MAX_uint32;
			for (const TPair<uint32, uint32>& P : Next) Start = FMath::Min(Start, P.Key);
			TArray<uint32> Loop = {Start};
			uint32 Cur = Next[Start];
			while (Cur != Start)
			{
				const uint32* Nx = Next.Find(Cur);
				if (!Nx || Loop.Num() > Next.Num()) return false;
				Loop.Add(Cur);
				Cur = *Nx;
			}
			if (Loop.Num() != Next.Num()) return false;
			for (uint32 V : Loop) Ring.Emplace(B.Verts[V * 3 + 0], B.Verts[V * 3 + 1]);
			double Area2 = 0.0;
			for (int32 i = 0; i < Ring.Num(); ++i)
			{
				const FVector2d& P = Ring[i];
				const FVector2d& Q = Ring[(i + 1) % Ring.Num()];
				Area2 += Cross(P.X, P.Y, Q.X, Q.Y);
			}
			if (Area2 <= 0.0) Algo::Reverse(Ring);
			return true;
		}

		void CleanRing(TArray<FVector2d>& Ring)
		{
			bool bChanged = true;
			while (bChanged && Ring.Num() >= 3)
			{
				bChanged = false;
				const int32 N = Ring.Num();
				for (int32 i = 0; i < N; ++i)
				{
					const FVector2d P = Ring[(i + N - 1) % N], Q = Ring[i], R = Ring[(i + 1) % N];
					const double Ax = Q.X - P.X, Ay = Q.Y - P.Y, Bx = R.X - Q.X, By = R.Y - Q.Y;
					const double La = FMath::Sqrt(Ax * Ax + Ay * Ay), Lb = FMath::Sqrt(Bx * Bx + By * By);
					if (La < EpsDup || (FMath::Abs(Cross(Ax, Ay, Bx, By)) <= CollinearSin * La * Lb && Ax * Bx + Ay * By > 0.0))
					{
						Ring.RemoveAt(i);
						bChanged = true;
						break;
					}
				}
			}
		}

		// ear clipping, fattest ear first (see building_roofs.triangulate)
		bool Triangulate(const TArray<FVector2d>& Ring, TArray<FIntVector3>& Ears)
		{
			TArray<int32> Idx;
			for (int32 i = 0; i < Ring.Num(); ++i) Idx.Add(i);
			auto DistSq = [](const FVector2d& P, const FVector2d& Q) { return (P.X - Q.X) * (P.X - Q.X) + (P.Y - Q.Y) * (P.Y - Q.Y); };
			while (Idx.Num() > 3)
			{
				const int32 M = Idx.Num();
				int32 BestK = -1;
				double BestFat = 0.0;
				for (int32 k = 0; k < M; ++k)
				{
					const int32 A = Idx[(k + M - 1) % M], Bi = Idx[k], C = Idx[(k + 1) % M];
					const FVector2d& Pa = Ring[A];
					const FVector2d& Pb = Ring[Bi];
					const FVector2d& Pc = Ring[C];
					if (Cross(Pb.X - Pa.X, Pb.Y - Pa.Y, Pc.X - Pb.X, Pc.Y - Pb.Y) <= 0.0) continue;
					bool bBlocked = false;
					for (int32 j : Idx)
					{
						if (j == A || j == Bi || j == C) continue;
						const FVector2d& Q = Ring[j];
						if (Cross(Pb.X - Pa.X, Pb.Y - Pa.Y, Q.X - Pa.X, Q.Y - Pa.Y) >= 0.0
							&& Cross(Pc.X - Pb.X, Pc.Y - Pb.Y, Q.X - Pb.X, Q.Y - Pb.Y) >= 0.0
							&& Cross(Pa.X - Pc.X, Pa.Y - Pc.Y, Q.X - Pc.X, Q.Y - Pc.Y) >= 0.0)
						{
							bBlocked = true;
							break;
						}
					}
					if (bBlocked) continue;
					const double Fat = Cross(Pb.X - Pa.X, Pb.Y - Pa.Y, Pc.X - Pa.X, Pc.Y - Pa.Y)
						/ (DistSq(Pa, Pb) + DistSq(Pb, Pc) + DistSq(Pc, Pa));
					if (BestK < 0 || Fat > BestFat)
					{
						BestFat = Fat;
						BestK = k;
					}
				}
				if (BestK < 0) return false;
				Ears.Add(FIntVector3(Idx[(BestK + M - 1) % M], Idx[BestK], Idx[(BestK + 1) % M]));
				Idx.RemoveAt(BestK);
			}
			Ears.Add(FIntVector3(Idx[0], Idx[1], Idx[2]));
			return true;
		}

		// minimum-area rectangle over the edge directions; l = long axis
		void Obb(const TArray<FVector2d>& Ring, double& Cx, double& Cy, double& Dx, double& Dy, double& HL, double& HS)
		{
			double LMin, LMax, SMin, SMax;
			auto Extent = [&](double Ux, double Uy)
			{
				LMin = SMin = DBL_MAX;
				LMax = SMax = -DBL_MAX;
				for (const FVector2d& P : Ring)
				{
					const double L = P.X * Ux + P.Y * Uy, S = -P.X * Uy + P.Y * Ux;
					LMin = FMath::Min(LMin, L); LMax = FMath::Max(LMax, L);
					SMin = FMath::Min(SMin, S); SMax = FMath::Max(SMax, S);
				}
			};
			const int32 N = Ring.Num();
			double BestArea = -1.0;
			Dx = 1.0; Dy = 0.0;
			for (int32 i = 0; i < N; ++i)
			{
				const double Ex = Ring[(i + 1) % N].X - Ring[i].X, Ey = Ring[(i + 1) % N].Y - Ring[i].Y;
				const double Le = FMath::Sqrt(Ex * Ex + Ey * Ey);
				if (Le <= 0.0) continue;
				Extent(Ex / Le, Ey / Le);
				const double Area = (LMax - LMin) * (SMax - SMin);
				if (BestArea < 0.0 || Area < BestArea)
				{
					BestArea = Area;
					Dx = Ex / Le;
					Dy = Ey / Le;
				}
			}
			Extent(Dx, Dy);
			if (LMax - LMin < SMax - SMin)
			{
				const double T = Dx;
				Dx = -Dy;
				Dy = T;
				Extent(Dx, Dy);
			}
			const double Lm = (LMax + LMin) / 2.0, Sm = (SMax + SMin) / 2.0;
			Cx = Lm * Dx - Sm * Dy;
			Cy = Lm * Dy + Sm * Dx;
			HL = (LMax - LMin) / 2.0;
			HS = (SMax - SMin) / 2.0;
		}

		// Sutherland-Hodgman against A*l + B*s <= C; crossings from Meet(line,
		// line) so two ears cutting one crease make the identical point;
		// points within EpsSnap of the plane are on it
		template <typename FMeet>
		void Clip(TArray<FPt>& Poly, double A, double B, double C, const FMeet& Meet)
		{
			FLine Cl;
			Cl.bCrease = true; Cl.A = A; Cl.B = B; Cl.C = C;
			const double Snap = EpsSnap * FMath::Sqrt(A * A + B * B);
			auto Side = [&](const FPt& P)
			{
				const double D = A * P.L + B * P.S - C;
				return FMath::Abs(D) < Snap ? 0.0 : D;
			};
			TArray<FPt> Out;
			const int32 N = Poly.Num();
			for (int32 i = 0; i < N; ++i)
			{
				const FPt& P = Poly[(i + N - 1) % N];
				FPt Q = Poly[i];
				const double Dp = Side(P), Dq = Side(Q);
				if (Dq <= 0.0)
				{
					if (Dp > 0.0 && Dq == 0.0)
					{
						Q.Line = Cl;   // q on the plane is the crossing
					}
					else if (Dp > 0.0)
					{
						const FVector2d X = Meet(Q.Line, Cl);
						Out.Add({X.X, X.Y, Cl});
					}
					Out.Add(Q);
				}
				else if (Dp < 0.0)
				{
					const FVector2d X = Meet(Q.Line, Cl);
					Out.Add({X.X, X.Y, Q.Line});
				}
			}
			Poly = MoveTemp(Out);
		}

		/** Replaces B's flat cap with a gable or hip roof plus gable end walls
		 *  (vertices from B.NumSolidVerts on); false = keep the flat cap. */
		bool PitchRoof(FParsedBuilding& B)
		{
			const int32 VCount = B.Verts.Num() / 3;
			if (B.FlatRoof || VCount == 0) return false;
			double Top = -DBL_MAX, Base = DBL_MAX, MinX = DBL_MAX, MinY = DBL_MAX;
			for (int32 v = 0; v < VCount; ++v)
			{
				MinX = FMath::Min(MinX, (double)B.Verts[v * 3 + 0]);
				MinY = FMath::Min(MinY, (double)B.Verts[v * 3 + 1]);
				Top = FMath::Max(Top, (double)B.Verts[v * 3 + 2]);
				Base = FMath::Min(Base, (double)B.Verts[v * 3 + 2]);
			}
			TArray<FVector2d> Ring;
			TArray<bool> IsCap;
			if (!FootprintRing(B, Top, Ring, IsCap)) return false;
			CleanRing(Ring);
			const int32 N = Ring.Num();
			if (N < 3) return false;
			double Area = 0.0;
			for (int32 i = 0; i < N; ++i) Area += Cross(Ring[i].X, Ring[i].Y, Ring[(i + 1) % N].X, Ring[(i + 1) % N].Y);
			Area /= 2.0;
			if (Area > MaxAreaM2 * 1e4) return false;
			double Cx, Cy, Dx, Dy, HL, HS;
			Obb(Ring, Cx, Cy, Dx, Dy, HL, HS);
			if (Area < MinFill * 4.0 * HL * HS || 2.0 * HS < MinSpanM * 100.0
				|| 2.0 * HS > MaxSpanM * 100.0 || 2.0 * HL > MaxLengthM * 100.0)
			{
				return false;
			}
			TArray<FIntVector3> Ears;
			if (!Triangulate(Ring, Ears)) return false;

			const uint32 H = Hash((int32)FMath::FloorToDouble(MinX), (int32)FMath::FloorToDouble(MinY));
			const bool bHip = (H & 0xFFFFu) / 65536.0 < HipFraction;
			const double Pitch = FMath::DegreesToRadians(PitchMinDeg + (PitchMaxDeg - PitchMinDeg) * (H >> 16) / 65536.0);
			const double K = FMath::Tan(Pitch), SlopeCm = FMath::Cos(Pitch) * FacadeTileCm;
			const double D = HL - HS;
			// one height field for every face (a crease point gets one height)
			auto Height = [&](double L, double S)
			{
				return FMath::Max(K * (bHip ? FMath::Min(HS - FMath::Abs(S), HL - FMath::Abs(L)) : HS - FMath::Abs(S)), 0.0);
			};
			struct FFace { int32 NumPlanes; double P[3][3]; bool bLong; };
			TArray<FFace> Faces;
			if (bHip)
			{
				Faces.Add({3, {{0, -1, 0}, {1, -1, D}, {-1, -1, D}}, true});
				Faces.Add({3, {{0, 1, 0}, {1, 1, D}, {-1, 1, D}}, true});
				Faces.Add({2, {{-1, 1, -D}, {-1, -1, -D}, {0, 0, 0}}, false});
				Faces.Add({2, {{1, 1, -D}, {1, -1, -D}, {0, 0, 0}}, false});
			}
			else
			{
				Faces.Add({1, {{0, -1, 0}, {0, 0, 0}, {0, 0, 0}}, true});
				Faces.Add({1, {{0, 1, 0}, {0, 0, 0}, {0, 0, 0}}, true});
			}

			TArray<FVector2d> Local;
			for (const FVector2d& P : Ring)
			{
				Local.Emplace((P.X - Cx) * Dx + (P.Y - Cy) * Dy, -(P.X - Cx) * Dy + (P.Y - Cy) * Dx);
			}
			auto Implicit = [&](const FLine& Ln, double& A, double& Bc, double& C)
			{
				if (Ln.bCrease)
				{
					A = Ln.A; Bc = Ln.B; C = Ln.C;
					return;
				}
				const FVector2d& Pi = Local[Ln.I];
				const FVector2d& Pj = Local[Ln.J];
				A = Pi.Y - Pj.Y; Bc = Pj.X - Pi.X; C = A * Pi.X + Bc * Pi.Y;
			};
			auto Meet = [&](const FLine& M, const FLine& Kl)
			{
				const bool bSwap = Kl < M;
				double A1, B1, C1, A2, B2, C2;
				Implicit(bSwap ? Kl : M, A1, B1, C1);
				Implicit(bSwap ? M : Kl, A2, B2, C2);
				const double Det = A1 * B2 - A2 * B1;
				return Det == 0.0 ? FVector2d::ZeroVector : FVector2d((C1 * B2 - C2 * B1) / Det, (A1 * C2 - A2 * C1) / Det);
			};
			auto IsFootprint = [&](const FLine& Ln) { return !Ln.bCrease && (Ln.J - Ln.I == 1 || (Ln.I == 0 && Ln.J == N - 1)); };

			// each ear clipped to each face -> convex pieces
			struct FPiece { TArray<FPt> Poly; bool bLong; };
			TArray<FPiece> Pieces;
			for (const FIntVector3& Ear : Ears)
			{
				const int32 E[3] = {Ear.X, Ear.Y, Ear.Z};
				for (const FFace& F : Faces)
				{
					TArray<FPt> Poly;
					for (int32 k = 0; k < 3; ++k)
					{
						const int32 i = E[k], j = E[(k + 2) % 3];
						FLine Ln;
						Ln.I = FMath::Min(i, j);
						Ln.J = FMath::Max(i, j);
						Poly.Add({Local[i].X, Local[i].Y, Ln});
					}
					for (int32 p = 0; p < F.NumPlanes && Poly.Num() >= 3; ++p)
					{
						Clip(Poly, F.P[p][0], F.P[p][1], F.P[p][2], Meet);
					}
					// a clip through a corner repeats it: drop the zero-length edge
					TArray<FPt> Kept;
					for (int32 j = 0; j < Poly.Num(); ++j)
					{
						const FPt& Pr = Poly[(j + Poly.Num() - 1) % Poly.Num()];
						if (Dist(Poly[j].L, Poly[j].S, Pr.L, Pr.S) >= EpsWeld) Kept.Add(Poly[j]);
					}
					if (Kept.Num() >= 3) Pieces.Add({MoveTemp(Kept), F.bLong});
				}
			}

			// a crease end on a diagonal can be a corner on one side of it
			// only: put it on the other side's edge too (no T-junction crack)
			TArray<FVector2d> Points;
			for (const FPiece& Pc : Pieces)
			{
				for (const FPt& Q : Pc.Poly) Points.AddUnique(FVector2d(Q.L, Q.S));
			}
			struct FFixed { TArray<FPt> Ring; bool bCentre = false; FVector2d Centre = FVector2d::ZeroVector; TArray<FIntVector3> Fan; bool bLong = false; };
			TArray<FFixed> Fixed;
			for (const FPiece& Pc : Pieces)
			{
				FFixed& Fx = Fixed.AddDefaulted_GetRef();
				Fx.bLong = Pc.bLong;
				const int32 M = Pc.Poly.Num();
				for (int32 j = 0; j < M; ++j)
				{
					const FPt& P = Pc.Poly[(j + M - 1) % M];
					const FPt& Q = Pc.Poly[j];
					const double Ex = Q.L - P.L, Ey = Q.S - P.S, Ll = Ex * Ex + Ey * Ey;
					TArray<TPair<double, FVector2d>> On;
					for (const FVector2d& R : Points)
					{
						const double T = ((R.X - P.L) * Ex + (R.Y - P.S) * Ey) / Ll;
						if (T > 0.0 && T < 1.0 && FMath::Abs(Cross(Ex, Ey, R.X - P.L, R.Y - P.S)) < EpsWeld * FMath::Sqrt(Ll)
							&& Dist(R.X, R.Y, P.L, P.S) >= EpsWeld && Dist(R.X, R.Y, Q.L, Q.S) >= EpsWeld)
						{
							On.Emplace(T, R);
						}
					}
					On.Sort([](const TPair<double, FVector2d>& X, const TPair<double, FVector2d>& Y) { return X.Key < Y.Key; });
					for (const TPair<double, FVector2d>& O : On) Fx.Ring.Add({O.Value.X, O.Value.Y, Q.Line});
					Fx.Ring.Add(Q);
				}
				Fx.bCentre = Fx.Ring.Num() > M;
				if (Fx.bCentre)
				{
					// fan from the centre: the corners now have collinear neighbours
					for (const FPt& Q : Pc.Poly) Fx.Centre += FVector2d(Q.L, Q.S);
					Fx.Centre /= M;
					const int32 F = Fx.Ring.Num();
					for (int32 j = 1; j <= F; ++j) Fx.Fan.Add(FIntVector3(0, j, j % F + 1));
				}
				else
				{
					for (int32 j = 1; j < M - 1; ++j) Fx.Fan.Add(FIntVector3(0, j, j + 1));
				}
			}
			auto FanPt = [](const FFixed& Fx, int32 i)
			{
				if (Fx.bCentre) return i == 0 ? Fx.Centre : FVector2d(Fx.Ring[i - 1].L, Fx.Ring[i - 1].S);
				return FVector2d(Fx.Ring[i].L, Fx.Ring[i].S);
			};

			// near-degenerate footprints can still leave a crack or a folded
			// sliver: the pieces must tile the footprint edge to edge (ends
			// within EpsWeld), else keep the flat cap
			double Tiled = 0.0;
			for (const FFixed& Fx : Fixed)
			{
				const int32 M = Fx.Ring.Num();
				for (int32 j = 0; j < M; ++j)
				{
					const FPt& P = Fx.Ring[(j + M - 1) % M];
					const FPt& Q = Fx.Ring[j];
					if (!IsFootprint(Q.Line))
					{
						bool bMatched = false;
						for (const FFixed& Gx : Fixed)
						{
							const int32 G = Gx.Ring.Num();
							for (int32 g = 0; g < G && !bMatched; ++g)
							{
								const FPt& P2 = Gx.Ring[(g + G - 1) % G];
								const FPt& Q2 = Gx.Ring[g];
								bMatched = Dist(Q.L, Q.S, P2.L, P2.S) < EpsWeld && Dist(P.L, P.S, Q2.L, Q2.S) < EpsWeld;
							}
							if (bMatched) break;
						}
						if (!bMatched) return false;
					}
					Tiled += Cross(P.L, P.S, Q.L, Q.S) / 2.0;
				}
				for (const FIntVector3& T : Fx.Fan)
				{
					const FVector2d P0 = FanPt(Fx, T.X), P1 = FanPt(Fx, T.Y), P2 = FanPt(Fx, T.Z);
					if (Cross(P1.X - P0.X, P1.Y - P0.Y, P2.X - P0.X, P2.Y - P0.Y) < -1e-6) return false;
				}
			}
			if (FMath::Abs(Tiled - Area) > 1.0 + 1e-6 * Area) return false;

			// walls kept (vertex order kept), cap dropped
			const int32 TCount = B.Tris.Num() / 3;
			TArray<int32> Remap;
			Remap.Init(-1, VCount);
			for (int32 t = 0; t < TCount; ++t)
			{
				if (IsCap[t]) continue;
				for (int32 k = 0; k < 3; ++k) Remap[B.Tris[t * 3 + k]] = 0;
			}
			TArray<float> OutV, OutUv;
			TArray<uint32> OutT;
			int32 NextId = 0;
			for (int32 v = 0; v < VCount; ++v)
			{
				if (Remap[v] < 0) continue;
				Remap[v] = NextId++;
				OutV.Append({B.Verts[v * 3 + 0], B.Verts[v * 3 + 1], B.Verts[v * 3 + 2]});
				OutUv.Append({B.Uvs[v * 2 + 0], B.Uvs[v * 2 + 1]});
			}
			struct FTopEdge { double Ax, Ay, Bx, By, Ua, Ub; };
			TArray<FTopEdge> TopEdges;
			for (int32 t = 0; t < TCount; ++t)
			{
				if (IsCap[t]) continue;
				uint32 AtTop[3];
				int32 NumAtTop = 0;
				for (int32 k = 0; k < 3; ++k)
				{
					const uint32 V = B.Tris[t * 3 + k];
					OutT.Add((uint32)Remap[V]);
					if (FMath::Abs((double)B.Verts[V * 3 + 2] - Top) < EpsZ) AtTop[NumAtTop++] = V;
				}
				if (NumAtTop == 2)
				{
					const uint32 A = AtTop[0], Bv = AtTop[1];
					TopEdges.Add({B.Verts[A * 3], B.Verts[A * 3 + 1], B.Verts[Bv * 3], B.Verts[Bv * 3 + 1], B.Uvs[A * 2], B.Uvs[Bv * 2]});
				}
			}

			auto World = [&](double L, double S) { return FVector2d(Cx + L * Dx - S * Dy, Cy + L * Dy + S * Dx); };
			struct FSkirt { FVector2d P0, P1; double H0, H1; };
			TArray<FSkirt> Skirts;
			for (const FFixed& Fx : Fixed)
			{
				const int32 First = OutV.Num() / 3;
				const int32 NumPts = Fx.Ring.Num() + (Fx.bCentre ? 1 : 0);
				for (int32 i = 0; i < NumPts; ++i)
				{
					const FVector2d P = FanPt(Fx, i);
					const FVector2d W = World(P.X, P.Y);
					OutV.Append({(float)W.X, (float)W.Y, (float)(Top + Height(P.X, P.Y))});
					// shingle rows along the eave, V down the slope
					if (Fx.bLong)
					{
						OutUv.Append({(float)(P.X / FacadeTileCm), (float)((FMath::Abs(P.Y) - HS) / SlopeCm)});
					}
					else
					{
						OutUv.Append({(float)(P.Y / FacadeTileCm), (float)((FMath::Abs(P.X) - HL) / SlopeCm)});
					}
				}
				for (const FIntVector3& T : Fx.Fan)
				{
					const FVector2d P0 = FanPt(Fx, T.X), P1 = FanPt(Fx, T.Y), P2 = FanPt(Fx, T.Z);
					if (FMath::Abs(Cross(P1.X - P0.X, P1.Y - P0.Y, P2.X - P0.X, P2.Y - P0.Y)) > 1e-6)
					{
						OutT.Append({(uint32)(First + T.X), (uint32)(First + T.Y), (uint32)(First + T.Z)});
					}
				}
				const int32 M = Fx.Ring.Num();
				for (int32 j = 0; j < M; ++j)
				{
					const FPt& P = Fx.Ring[(j + M - 1) % M];
					const FPt& Q = Fx.Ring[j];
					if (IsFootprint(Q.Line))
					{
						Skirts.Add({World(P.L, P.S), World(Q.L, Q.S), Height(P.L, P.S), Height(Q.L, Q.S)});
					}
				}
			}

			// gable end walls under the roof, the wall's U continued
			const int32 NumSolid = OutV.Num() / 3;
			for (FSkirt Sk : Skirts)
			{
				const double Len = Dist(Sk.P0.X, Sk.P0.Y, Sk.P1.X, Sk.P1.Y);
				if (FMath::Max(Sk.H0, Sk.H1) < EpsWeld || Len < EpsWeld) continue;
				const double Mx = (Sk.P0.X + Sk.P1.X) / 2.0, My = (Sk.P0.Y + Sk.P1.Y) / 2.0;
				double Best = -1.0, U0 = 0.0, U1 = Len / FacadeTileCm;
				for (const FTopEdge& E : TopEdges)
				{
					const double Ex = E.Bx - E.Ax, Ey = E.By - E.Ay, Ll = Ex * Ex + Ey * Ey;
					if (Ll <= 0.0) continue;
					const double T = FMath::Clamp(((Mx - E.Ax) * Ex + (My - E.Ay) * Ey) / Ll, 0.0, 1.0);
					const double Dd = Dist(E.Ax + Ex * T, E.Ay + Ey * T, Mx, My);
					if (Best < 0.0 || Dd < Best)
					{
						Best = Dd;
						U0 = E.Ua + (E.Ub - E.Ua) * ((Sk.P0.X - E.Ax) * Ex + (Sk.P0.Y - E.Ay) * Ey) / Ll;
						U1 = E.Ua + (E.Ub - E.Ua) * ((Sk.P1.X - E.Ax) * Ex + (Sk.P1.Y - E.Ay) * Ey) / Ll;
					}
				}
				if (U1 < U0)   // wind like the wall below (U increasing along the edge)
				{
					Swap(Sk.P0, Sk.P1);
					Swap(Sk.H0, Sk.H1);
					Swap(U0, U1);
				}
				struct FCorner { FVector2d P; double Z, U; };
				TArray<FCorner, TInlineAllocator<4>> Corners;
				Corners.Add({Sk.P0, Top, U0});
				Corners.Add({Sk.P1, Top, U1});
				if (Sk.H1 >= EpsWeld) Corners.Add({Sk.P1, Top + Sk.H1, U1});
				if (Sk.H0 >= EpsWeld) Corners.Add({Sk.P0, Top + Sk.H0, U0});
				const int32 First = OutV.Num() / 3;
				for (const FCorner& C : Corners)
				{
					OutV.Append({(float)C.P.X, (float)C.P.Y, (float)C.Z});
					OutUv.Append({(float)C.U, (float)((C.Z - Base) / FacadeTileCm)});
				}
				for (int32 j = 1; j < Corners.Num() - 1; ++j)
				{
					OutT.Append({(uint32)First, (uint32)(First + j), (uint32)(First + j + 1)});
				}
			}

			B.Verts = MoveTemp(OutV);
			B.Uvs = MoveTemp(OutUv);
			B.Tris = MoveTemp(OutT);
			B.NumSolidVerts = NumSolid;
			return true;
		}
	}

	/** Realistic only: pitch what qualifies; whatever keeps its flat cap
	 *  reads as a concrete flat roof (no flat shingles). Worker thread. */
	int32 ApplyRealisticRoofs(FParsedBuildingTile& Tile)
	{
		int32 NumPitched = 0;
		for (FParsedBuilding& B : Tile.Buildings)
		{
			if (Roof::PitchRoof(B))
			{
				++NumPitched;
			}
			else
			{
				B.FlatRoof = 1;
			}
		}
		return NumPitched;
	}
}

AODBuildingManager::AODBuildingManager()
{
	PrimaryActorTick.bCanEverTick = true;
	RootComponent = CreateDefaultSubobject<USceneComponent>(TEXT("Root"));
}

void AODBuildingManager::BeginPlay()
{
	Super::BeginPlay();

	FacadeMat = ODVisualStyle::Pick(LoadObject<UMaterialInterface>(
		nullptr, TEXT("/Game/OpenDrive/Materials/M_BuildingFacade.M_BuildingFacade")));
	// Realistic splits roofs into their own section (see RoofMat); the roof
	// material has no stylized twin, so it is loaded directly, not Picked.
	RoofMat = ODVisualStyle::IsRealistic()
		? LoadObject<UMaterialInterface>(nullptr,
			TEXT("/Game/OpenDrive/Materials/M_BuildingRoof_real.M_BuildingRoof_real"),
			nullptr, LOAD_NoWarn | LOAD_Quiet)
		: nullptr;

	ClampSpawnRadiusToTerrainCollision();
}

void AODBuildingManager::ClampSpawnRadiusToTerrainCollision()
{
	const AODTerrainManager* Terrain = nullptr;
	for (TActorIterator<AODTerrainManager> It(GetWorld()); It; ++It)
	{
		Terrain = *It;
		break;
	}
	if (!Terrain)
	{
		return;   // no terrain manager in this level - nothing to clamp against
	}

	// Small safety margin below the terrain's own near-tier collision ring -
	// a building tile requested right at that exact boundary would otherwise
	// race the ring's own per-tick recentering around the player.
	constexpr float SafetyMarginCm = 20000.0f;   // 200 m
	const float MaxSafeRadius = FMath::Max(0.0f, Terrain->CollisionRadius - SafetyMarginCm);
	if (SpawnRadius > MaxSafeRadius)
	{
		UE_LOG(LogTemp, Warning,
			TEXT("ODBuildingManager: SpawnRadius %.0f m exceeds AODTerrainManager's "
			     "CollisionRadius %.0f m (minus a %.0f m safety margin) - buildings "
			     "out that far would never find real ground collision to trace "
			     "against (terrain only keeps collision within CollisionRadius of "
			     "the player, no matter how long placement waits), so every one of "
			     "them would just churn through the retry queue until it times out "
			     "and gets dropped. Clamping SpawnRadius down to %.0f m for this run."),
			SpawnRadius / 100.0, Terrain->CollisionRadius / 100.0, SafetyMarginCm / 100.0,
			MaxSafeRadius / 100.0);
		SpawnRadius = MaxSafeRadius;
	}
}

void AODBuildingManager::EndPlay(const EEndPlayReason::Type EndPlayReason)
{
	if (PendingTiles.Num() > 0)
	{
		UE_LOG(LogTemp, Display,
			TEXT("ODBuildingManager: EndPlay with %d tile load(s) still in flight - "
			     "their completions will no-op harmlessly when they land"),
			PendingTiles.Num());
	}
	Super::EndPlay(EndPlayReason);
}

bool AODBuildingManager::ParseBuildingTileFile(const FString& Path, FParsedBuildingTile& Out)
{
	TArray<uint8> Bin;
	if (!FFileHelper::LoadFileToArray(Bin, *Path) || Bin.Num() < 4 + GenerationIdBytes + 4
		|| Bin[0] != 'O' || Bin[1] != 'D' || Bin[2] != 'U' || Bin[3] != '2')
	{
		return false;
	}

	const uint8* Cur = Bin.GetData() + 4;
	const uint8* End = Bin.GetData() + Bin.Num();
	auto Remaining = [&]() -> int64 { return (int64)(End - Cur); };

	if (Remaining() < GenerationIdBytes)
	{
		return false;
	}
	Out.GenerationId = ReadGenerationId(Cur);
	Cur += GenerationIdBytes;

	if (Remaining() < 4)
	{
		return false;
	}
	const uint32 BuildingCount = ReadU32LE(Cur);
	Cur += 4;

	Out.Buildings.Reserve(BuildingCount);
	for (uint32 b = 0; b < BuildingCount; ++b)
	{
		if (Remaining() < 10)
		{
			return false;
		}
		const uint32 VCount = ReadU32LE(Cur); Cur += 4;
		const uint32 TCount = ReadU32LE(Cur); Cur += 4;
		const uint8 WallSet = *Cur++;
		const uint8 FlatRoof = *Cur++;
		const int64 NeedBytes = (int64)VCount * 12 + (int64)VCount * 8 + (int64)TCount * 12;
		if (Remaining() < NeedBytes)
		{
			return false;
		}

		const uint8* VertsBase = Cur;
		const uint8* UvBase = Cur + (int64)VCount * 12;
		const uint8* TrisBase = Cur + (int64)VCount * 12 + (int64)VCount * 8;
		Cur += NeedBytes;

		FParsedBuilding& Building = Out.Buildings.AddDefaulted_GetRef();
		Building.WallSet = WallSet;
		Building.FlatRoof = FlatRoof;
		Building.Verts.SetNumUninitialized(VCount * 3);
		for (uint32 v = 0; v < VCount; ++v)
		{
			Building.Verts[v * 3 + 0] = ReadF32LE(VertsBase + v * 12 + 0);
			Building.Verts[v * 3 + 1] = ReadF32LE(VertsBase + v * 12 + 4);
			Building.Verts[v * 3 + 2] = ReadF32LE(VertsBase + v * 12 + 8);
		}
		Building.Uvs.SetNumUninitialized(VCount * 2);
		for (uint32 v = 0; v < VCount; ++v)
		{
			Building.Uvs[v * 2 + 0] = ReadF32LE(UvBase + v * 8 + 0);
			Building.Uvs[v * 2 + 1] = ReadF32LE(UvBase + v * 8 + 4);
		}
		Building.Tris.SetNumUninitialized(TCount * 3);
		for (uint32 t = 0; t < TCount; ++t)
		{
			Building.Tris[t * 3 + 0] = ReadU32LE(TrisBase + t * 12 + 0);
			Building.Tris[t * 3 + 1] = ReadU32LE(TrisBase + t * 12 + 4);
			Building.Tris[t * 3 + 2] = ReadU32LE(TrisBase + t * 12 + 8);
		}
	}
	return true;
}

void AODBuildingManager::ProfileRoofTile(const FString& Path, int32& Buildings, int32& Pitched,
	int32& TrisBefore, int32& TrisAfter, float& RoofMs)
{
	Buildings = Pitched = TrisBefore = TrisAfter = 0;
	RoofMs = 0.0f;
	FParsedBuildingTile Parsed;
	if (!ParseBuildingTileFile(Path, Parsed))
	{
		return;
	}
	Buildings = Parsed.Buildings.Num();
	for (const FParsedBuilding& B : Parsed.Buildings) TrisBefore += B.Tris.Num() / 3;
	const double T0 = FPlatformTime::Seconds();
	Pitched = ApplyRealisticRoofs(Parsed);
	RoofMs = (float)((FPlatformTime::Seconds() - T0) * 1000.0);
	for (const FParsedBuilding& B : Parsed.Buildings) TrisAfter += B.Tris.Num() / 3;
}

void AODBuildingManager::RequestTileLoad(const FIntPoint& Key)
{
	PendingTiles.Add(Key);
	const FString Path = FPaths::ProjectContentDir() / TEXT("OpenDrive/Masks")
		/ FString::Printf(TEXT("%s_tile_%d_%d.bin"), *BuildingTilePrefix, Key.X, Key.Y);

	TWeakObjectPtr<AODBuildingManager> WeakThis(this);
	const bool bRealistic = ODVisualStyle::IsRealistic();
	Async(EAsyncExecution::ThreadPool, [WeakThis, Key, Path, bRealistic]()
	{
		FParsedBuildingTile Parsed;
		FString ReadPath;
		const EODTileSource Src = ODTileSource::Resolve(Path, ReadPath);
		const bool bOk = Src == EODTileSource::Ready && ParseBuildingTileFile(ReadPath, Parsed);
		const bool bPending = Src == EODTileSource::Pending;
		if (bOk && bRealistic)
		{
			ApplyRealisticRoofs(Parsed);   // pitched roofs, off the game thread
		}
		AsyncTask(ENamedThreads::GameThread,
			[WeakThis, Key, bOk, bPending, Parsed = MoveTemp(Parsed)]() mutable
			{
				if (!WeakThis.IsValid() || WeakThis->GetWorld() == nullptr)
				{
					return;
				}
				if (bPending)
				{
					ODTileSource::RetryLater(WeakThis.Get(), WeakThis->PendingTiles, Key);
					return;
				}
				WeakThis->OnTileParsed(Key, bOk, MoveTemp(Parsed));
			});
	});
}

void AODBuildingManager::OnTileParsed(const FIntPoint& Key, bool bOk, FParsedBuildingTile&& Parsed)
{
	PendingTiles.Remove(Key);
	if (!bOk)
	{
		// expected for the vast majority of tiles - most of the map has no
		// buildings at all, same "no hole" semantics as every other
		// tile-streamed system here
		MissingTiles.Add(Key);
		return;
	}

	if (!bHasFirstGenerationId)
	{
		FirstGenerationId = Parsed.GenerationId;
		bHasFirstGenerationId = true;
	}
	else if (!bGenerationMismatchWarned && Parsed.GenerationId != FirstGenerationId)
	{
		bGenerationMismatchWarned = true;
		UE_LOG(LogTemp, Warning,
			TEXT("ODBuildingManager: tile (%d,%d) generation id %s does not match "
			     "%s's first-loaded generation id %s - tiles from different pipeline "
			     "runs may be mixed (AUDIT-RELIABILITY.md SS3.1/SS5.1)"),
			Key.X, Key.Y, *Parsed.GenerationId, *BuildingTilePrefix, *FirstGenerationId);
	}

	FinalizeTile(Key, MoveTemp(Parsed));
}

bool AODBuildingManager::GroundAt(double WorldX, double WorldY, const AActor* Ignore, float& OutZ) const
{
	FHitResult Hit;
	const FVector Start(WorldX, WorldY, PlayerZHint + GroundTraceMarginCm);
	const FVector End(WorldX, WorldY, PlayerZHint - GroundTraceMarginCm);
	FCollisionQueryParams Params;
	Params.AddIgnoredActor(this);          // our own building collision
	if (Ignore)
	{
		Params.AddIgnoredActor(Ignore);    // the player's car
	}
	if (!GetWorld()->LineTraceSingleByChannel(Hit, Start, End, ECC_WorldStatic, Params))
	{
		++NoHitRejectCount;
		return false;
	}
	// Same suspected-phantom-surface guard as AODForestManager::GroundAt() -
	// see this class's header comment on GroundSanityMaxDeltaCm: local relief
	// within one tile shouldn't plausibly exceed this from the player's own
	// settled height, so a hit further off is almost certainly the known
	// phantom collision surface at old-origin height, not real ground.
	if (FMath::Abs(Hit.ImpactPoint.Z - PlayerZHint) > GroundSanityMaxDeltaCm)
	{
		++PhantomRejectCount;
		return false;
	}
	OutZ = Hit.ImpactPoint.Z;
	return true;
}

bool AODBuildingManager::ComputeBuildingZOffset(const FParsedBuilding& B, const AActor* Ignore, float& OutOffsetCm) const
{
	const uint32 VCount = B.Verts.Num() / 3;
	if (VCount == 0)
	{
		return false;
	}

	// footprint base = the lowest baked Z among the building's own verts (the
	// extrusion's bottom ring) - everything else (walls, roof) sits above it
	// at a fixed baked offset, so translating the whole mesh by one Z delta
	// keeps that shape intact (no tilt, no per-vertex distortion).
	float BakedBaseZ = TNumericLimits<float>::Max();
	for (uint32 v = 0; v < VCount; ++v)
	{
		BakedBaseZ = FMath::Min(BakedBaseZ, B.Verts[v * 3 + 2]);
	}

	// Distinct XY corners at (approximately) the base ring - dedup so a dense
	// footprint polygon doesn't turn into dozens of redundant traces at the
	// same point. Capped so a pathological high-vertex-count footprint can't
	// blow up finalize-time cost.
	constexpr float ZEpsilonCm = 2.0f;
	constexpr float DedupEpsilonSqCm = 25.0f;   // 5 cm
	constexpr int32 MaxCorners = 32;
	TArray<FVector2D, TInlineAllocator<MaxCorners>> Corners;
	for (uint32 v = 0; v < VCount && Corners.Num() < MaxCorners; ++v)
	{
		if (FMath::Abs(B.Verts[v * 3 + 2] - BakedBaseZ) > ZEpsilonCm)
		{
			continue;
		}
		const FVector2D XY(B.Verts[v * 3 + 0], B.Verts[v * 3 + 1]);
		bool bDup = false;
		for (const FVector2D& Existing : Corners)
		{
			if (FVector2D::DistSquared(Existing, XY) < DedupEpsilonSqCm)
			{
				bDup = true;
				break;
			}
		}
		if (!bDup)
		{
			Corners.Add(XY);
		}
	}

	// Lowest successfully-traced corner - so a footprint spanning a slope
	// gets its base planted at the low side (minus EmbedMarginCm) rather than
	// averaging (which would float the low corner) or using any single
	// arbitrary corner (which could float the others).
	// Corners more than CornerPitMaxDropCm below the building's highest hit
	// are ignored: one footprint can't span that much real relief, so it's a
	// terrain spike (e.g. a DEM-void pit) that would sink the whole building.
	TArray<float, TInlineAllocator<MaxCorners>> HitZs;
	float HighestGroundZ = -TNumericLimits<float>::Max();
	for (const FVector2D& XY : Corners)
	{
		float Z;
		if (GroundAt(XY.X, XY.Y, Ignore, Z))
		{
			HitZs.Add(Z);
			HighestGroundZ = FMath::Max(HighestGroundZ, Z);
		}
	}
	if (HitZs.Num() == 0)
	{
		return false;
	}
	float LowestGroundZ = HighestGroundZ;
	for (const float Z : HitZs)
	{
		if (Z >= HighestGroundZ - CornerPitMaxDropCm)
		{
			LowestGroundZ = FMath::Min(LowestGroundZ, Z);
		}
	}

	OutOffsetCm = (LowestGroundZ - EmbedMarginCm) - BakedBaseZ;
	return true;
}

bool AODBuildingManager::IsNearRoadCorridor(double WorldX, double WorldY)
{
	if (!bCorridorSearched)
	{
		bCorridorSearched = true;
		// this area's own road manager, matched by prefix (2026-10-05, large
		// world: several area sets can be live, first-found may be another's)
		FString Area = BuildingTilePrefix;
		Area.RemoveFromStart(TEXT("buildings_"));
		for (TActorIterator<AODRoadManager> It(GetWorld()); It; ++It)
		{
			if (It->RoadTilePrefix == TEXT("road_") + Area)
			{
				CorridorCenterline = It->RoadCenterline;   // world cm, already loaded
				break;
			}
		}
		for (int32 i = 0; i < CorridorCenterline.Num(); ++i)
		{
			const int32 CellX = FMath::FloorToInt(CorridorCenterline[i].X / CorridorCellCm);
			const int32 CellY = FMath::FloorToInt(CorridorCenterline[i].Y / CorridorCellCm);
			CorridorBuckets.FindOrAdd(FIntPoint(CellX, CellY)).Add(i);
		}
	}
	if (CorridorCenterline.Num() == 0)
	{
		return true;   // no centerline data - fail open, everything collides
	}

	const double BandSq = (double)CollisionBandCm * CollisionBandCm;
	const int32 CellX = FMath::FloorToInt(WorldX / CorridorCellCm);
	const int32 CellY = FMath::FloorToInt(WorldY / CorridorCellCm);
	// CollisionBandCm can exceed CorridorCellCm if a level tunes it up -
	// widen the ring accordingly instead of silently under-searching.
	const int32 Reach = FMath::Max(1, FMath::CeilToInt(CollisionBandCm / CorridorCellCm));
	for (int32 dy = -Reach; dy <= Reach; ++dy)
	{
		for (int32 dx = -Reach; dx <= Reach; ++dx)
		{
			const TArray<int32>* Bucket = CorridorBuckets.Find(FIntPoint(CellX + dx, CellY + dy));
			if (!Bucket)
			{
				continue;
			}
			for (const int32 i : *Bucket)
			{
				const double Dx = CorridorCenterline[i].X - WorldX;
				const double Dy = CorridorCenterline[i].Y - WorldY;
				if (Dx * Dx + Dy * Dy <= BandSq)
				{
					return true;
				}
			}
		}
	}
	return false;
}

void AODBuildingManager::AppendBuildingsToTileMesh(FBuildingTile& Tile, bool bNear,
	const TArray<TPair<const FParsedBuilding*, float>>& Batch)
{
	if (Batch.Num() == 0)
	{
		return;
	}

	TObjectPtr<UDynamicMeshComponent>& CompSlot = bNear ? Tile.NearMesh : Tile.FarMesh;
	if (!CompSlot)
	{
		auto* Comp = NewObject<UDynamicMeshComponent>(this);
		Comp->SetupAttachment(RootComponent);
		const TArray<float>& V0 = Batch[0].Key->Verts;
		if (V0.Num() >= 3)
		{
			Comp->SetRelativeLocation(FVector(V0[0], V0[1], 0.0));   // local origin: see AODRoadManager::CreateChunkMeshComponent (large-world precision)
		}
		Comp->RegisterComponent();
		if (bNear)
		{
			// Async cook (2026-09-07 frame-drop pass): a whole tile's merged
			// building geometry cooked synchronously on the game thread
			// otherwise - same flag the road/terrain/wall/guardrail managers
			// use. Buildings sit off the pavement, so a body that's a frame
			// or two late is unreachable anyway.
			Comp->bUseAsyncCooking = true;
			Comp->EnableComplexAsSimpleCollision();
			Comp->SetCollisionProfileName(TEXT("BlockAll"));
		}
		else
		{
			// out of driving reach - collision cooking is the expensive half
			// of a merged tile mesh, and it would never be touched
			Comp->SetCollisionEnabled(ECollisionEnabled::NoCollision);
		}
		if (FacadeMat)
		{
			Comp->SetMaterial(0, FacadeMat);
		}
		if (RoofMat)
		{
			Comp->SetMaterial(1, RoofMat);
		}
		CompSlot = Comp;
	}

	const FVector3d O = CompSlot->GetRelativeLocation();
	CompSlot->EditMesh([&](FDynamicMesh3& Mesh)
	{
		Mesh.EnableAttributes();
		FDynamicMeshUVOverlay* UV = Mesh.Attributes()->PrimaryUV();
		Mesh.Attributes()->EnablePrimaryColors();
		FDynamicMeshColorOverlay* Color = Mesh.Attributes()->PrimaryColors();
		FDynamicMeshMaterialAttribute* MatId = nullptr;
		if (RoofMat)
		{
			Mesh.Attributes()->EnableMaterialID();
			MatId = Mesh.Attributes()->GetMaterialID();
		}

		for (const auto& Entry : Batch)
		{
			const FParsedBuilding& B = *Entry.Key;
			// Pure vertical translation only - never a rotation - so the
			// building stays upright regardless of local terrain slope
			// (task spec: "no tilt from terrain normal"). Baked into the
			// vertices now: a merged mesh has no per-building transform.
			const float ZOffsetCm = Entry.Value;
			const uint32 VCount = B.Verts.Num() / 3;
			const uint32 TCount = B.Tris.Num() / 3;

			// Same per-building facade selectors on every vertex (see
			// BuildingVertexColorForFacade()'s comment for why vertex
			// color, not a per-building MID, is this streaming
			// architecture's instancing-safe variation channel - and why
			// merging costs nothing: the variation already travels
			// per-vertex, not per-component).
			const FVector4f FacadeColor = BuildingVertexColorForFacade(B);

			TArray<int32> VertIds; VertIds.SetNumUninitialized(VCount);
			TArray<int32> UvIds; UvIds.SetNumUninitialized(VCount);
			TArray<int32> ColorIds; ColorIds.SetNumUninitialized(VCount);

			for (uint32 v = 0; v < VCount; ++v)
			{
				VertIds[v] = Mesh.AppendVertex(FVector3d(
					B.Verts[v * 3 + 0] - O.X, B.Verts[v * 3 + 1] - O.Y,
					B.Verts[v * 3 + 2] + ZOffsetCm));
			}
			for (uint32 v = 0; v < VCount; ++v)
			{
				UvIds[v] = UV->AppendElement(
					FVector2f(B.Uvs[v * 2 + 0], B.Uvs[v * 2 + 1]));
			}
			for (uint32 v = 0; v < VCount; ++v)
			{
				// Vertex color here is NOT sRGB display color - see
				// BuildingVertexColorForFacade()'s comment, these are raw
				// [0,1) selector/seed values the material reads back
				// directly (default linear-in/linear-out, no
				// SetVertexColorSpaceTransformMode).
				// A = 0 on gable end walls: no windows there
				ColorIds[v] = Color->AppendElement(FVector4f(FacadeColor.X, FacadeColor.Y, FacadeColor.Z,
					(int32)v < B.NumSolidVerts ? 1.0f : 0.0f));
			}
			for (uint32 t = 0; t < TCount; ++t)
			{
				const uint32 I0 = B.Tris[t * 3 + 0];
				const uint32 I1 = B.Tris[t * 3 + 1];
				const uint32 I2 = B.Tris[t * 3 + 2];
				const int32 Tid = Mesh.AppendTriangle(VertIds[I0], VertIds[I1], VertIds[I2]);
				if (Tid >= 0)
				{
					UV->SetTriangle(Tid, FIndex3i(UvIds[I0], UvIds[I1], UvIds[I2]));
					Color->SetTriangle(Tid, FIndex3i(ColorIds[I0], ColorIds[I1], ColorIds[I2]));
					if (MatId)
					{
						// roof = faces up, the same 0.55 cut as the stylized
						// material's normal-Z roof mask (ROOF_MASK_THRESHOLD)
						MatId->SetValue(Tid, Mesh.GetTriNormal(Tid).Z > 0.55 ? 1 : 0);
					}
				}
			}
		}

		// Per-vertex normals, averaged only over triangles sharing the same
		// vertex ID - extrude_footprint() never shares a vertex between two
		// wall quads (or between wall top ring and roof fan), and the merge
		// never welds vertices between buildings, so this still yields
		// crisp flat-per-quad walls and flat roofs across the whole merged
		// mesh (M_BuildingFacade's wall-vs-roof mask keys off normal Z).
		// Recomputing over already-appended buildings on a retry batch is
		// redundant but yields identical values, so it's only a little
		// wasted work on the rare deferred-retry path.
		FMeshNormals::InitializeOverlayToPerVertexNormals(Mesh.Attributes()->PrimaryNormals(), false);
	});

	Tile.NumBuildings += Batch.Num();
}

void AODBuildingManager::FinalizeTile(const FIntPoint& Key, FParsedBuildingTile&& Parsed)
{
	FBuildingTile& Tile = Tiles.Add(Key);

	const APawn* Player = UGameplayStatics::GetPlayerPawn(this, 0);
	const double Now = GetWorld()->GetTimeSeconds();
	int32 NumDeferred = 0;

	// 2026-09-06 (AUDIT.md rec. 2): partition into the tile's two merged
	// meshes - near-corridor (collision) vs far (visual only) - and append
	// each side as ONE batch, so the whole tile costs two mesh builds and
	// at most one collision cook instead of one per building.
	TArray<TPair<const FParsedBuilding*, float>> NearBatch, FarBatch;
	NearBatch.Reserve(Parsed.Buildings.Num());

	for (const FParsedBuilding& B : Parsed.Buildings)
	{
		float ZOffsetCm = 0.0f;
		if (!ComputeBuildingZOffset(B, Player, ZOffsetCm))
		{
			// No usable ground trace anywhere on this building's footprint YET
			// - most likely AODTerrainManager hasn't finished streaming
			// collision under this specific spot even though the ground-snap
			// gate in Tick() confirmed the PLAYER'S own local ground is real
			// (that gate says nothing about the rest of the SpawnRadius disc,
			// and in a fast/headless run many building tiles can finalize
			// within the same fraction of a second the gate opens). Queue for
			// retry (see ProcessPendingBuildings()) instead of dropping it -
			// unlike a forest scatter candidate, a specific OSM building
			// footprint isn't interchangeable with any other, so silently
			// losing one to a streaming race is a much more visible
			// regression than one fewer tree in a dense stand.
			PendingBuildings.Add({Key, B, Now, Now});
			++NumDeferred;
			continue;
		}
		MinPlacementDeltaCm = FMath::Min(MinPlacementDeltaCm, ZOffsetCm);
		MaxPlacementDeltaCm = FMath::Max(MaxPlacementDeltaCm, ZOffsetCm);
		++PlacedCount;

		double CentroidX = 0.0, CentroidY = 0.0;
		const uint32 VCount = B.Verts.Num() / 3;
		for (uint32 v = 0; v < VCount; ++v)
		{
			CentroidX += B.Verts[v * 3 + 0];
			CentroidY += B.Verts[v * 3 + 1];
		}
		if (VCount > 0)
		{
			CentroidX /= VCount;
			CentroidY /= VCount;
		}
		(IsNearRoadCorridor(CentroidX, CentroidY) ? NearBatch : FarBatch)
			.Emplace(&B, ZOffsetCm);
	}

	AppendBuildingsToTileMesh(Tile, true, NearBatch);
	AppendBuildingsToTileMesh(Tile, false, FarBatch);

	UE_LOG(LogTemp, Display,
		TEXT("ODBuildingManager: tile (%d,%d) parsed %d building(s), %d placed "
		     "(%d collidable near-corridor + %d visual-only far) into %d merged "
		     "component(s), %d deferred pending ground trace retry"),
		Key.X, Key.Y, Parsed.Buildings.Num(), Parsed.Buildings.Num() - NumDeferred,
		NearBatch.Num(), FarBatch.Num(),
		(Tile.NearMesh ? 1 : 0) + (Tile.FarMesh ? 1 : 0), NumDeferred);
}

void AODBuildingManager::ReleaseTile(const FIntPoint& Key)
{
	if (FBuildingTile* Tile = Tiles.Find(Key))
	{
		if (Tile->NearMesh)
		{
			Tile->NearMesh->DestroyComponent();
		}
		if (Tile->FarMesh)
		{
			Tile->FarMesh->DestroyComponent();
		}
		Tiles.Remove(Key);
	}
}

void AODBuildingManager::ProcessPendingBuildings()
{
	if (PendingBuildings.Num() == 0)
	{
		return;
	}
	const APawn* Player = UGameplayStatics::GetPlayerPawn(this, 0);
	const double Now = GetWorld()->GetTimeSeconds();

	// 2026-09-07 frame-drop pass: this used to call AppendBuildingsToTileMesh
	// with a batch of ONE inside the loop - each resolved building triggered
	// a full EditMesh + render-state rebuild + collision re-cook of the
	// entire tile's merged near mesh, several times per frame while the
	// deferral queue drained. Now: pass 1 resolves ground offsets (capped -
	// each attempt is up to 32 line traces), grouping resolved entries by
	// (tile, near/far); pass 2 appends each group with ONE mesh edit; pass 3
	// removes resolved/expired entries. Indices collected in pass 1 stay
	// valid through pass 2 because nothing is removed until pass 3, and
	// pass 3 removes in descending index order so RemoveAtSwap never
	// disturbs a lower pending index.
	constexpr int32 MaxResolveAttemptsPerTick = 8;
	int32 Attempts = 0;
	TMap<TPair<FIntPoint, bool>, TArray<TPair<const FParsedBuilding*, float>>> Resolved;
	TArray<int32, TInlineAllocator<16>> DoneIndices;

	for (int32 i = 0; i < PendingBuildings.Num(); ++i)
	{
		FPendingBuilding& Pending = PendingBuildings[i];

		if (!Tiles.Contains(Pending.TileKey))
		{
			// Tile was released (player drove out of range) before this
			// building ever resolved - about to be out of despawn range
			// anyway, so just drop it rather than place something that would
			// be torn down again almost immediately.
			DoneIndices.Add(i);
			continue;
		}
		if (Now < Pending.NextRetryTime)
		{
			continue;
		}
		if (Attempts >= MaxResolveAttemptsPerTick)
		{
			break;   // trace budget spent - the rest keep their NextRetryTime
		}
		++Attempts;

		float ZOffsetCm = 0.0f;
		if (ComputeBuildingZOffset(Pending.Building, Player, ZOffsetCm))
		{
			MinPlacementDeltaCm = FMath::Min(MinPlacementDeltaCm, ZOffsetCm);
			MaxPlacementDeltaCm = FMath::Max(MaxPlacementDeltaCm, ZOffsetCm);
			++PlacedCount;
			double CentroidX = 0.0, CentroidY = 0.0;
			const uint32 VCount = Pending.Building.Verts.Num() / 3;
			for (uint32 v = 0; v < VCount; ++v)
			{
				CentroidX += Pending.Building.Verts[v * 3 + 0];
				CentroidY += Pending.Building.Verts[v * 3 + 1];
			}
			if (VCount > 0)
			{
				CentroidX /= VCount;
				CentroidY /= VCount;
			}
			Resolved.FindOrAdd({Pending.TileKey, IsNearRoadCorridor(CentroidX, CentroidY)})
				.Emplace(&Pending.Building, ZOffsetCm);
			DoneIndices.Add(i);
			continue;
		}

		if (Now - Pending.FirstAttemptTime > PendingGiveUpAfterSec)
		{
			++GivenUpCount;
			UE_LOG(LogTemp, Warning,
				TEXT("ODBuildingManager: gave up placing a building in tile (%d,%d) "
				     "after %.1f s - no valid ground trace anywhere on its footprint "
				     "(genuine data gap, not just a streaming race)"),
				Pending.TileKey.X, Pending.TileKey.Y, PendingGiveUpAfterSec);
			DoneIndices.Add(i);
			continue;
		}

		Pending.NextRetryTime = Now + PendingRetryIntervalSec;
	}

	for (auto& Group : Resolved)
	{
		if (FBuildingTile* Tile = Tiles.Find(Group.Key.Key))
		{
			AppendBuildingsToTileMesh(*Tile, Group.Key.Value, Group.Value);
		}
	}
	for (int32 d = DoneIndices.Num() - 1; d >= 0; --d)
	{
		PendingBuildings.RemoveAtSwap(DoneIndices[d]);
	}
}

void AODBuildingManager::Tick(float DeltaSeconds)
{
	Super::Tick(DeltaSeconds);

	const APawn* Player = UGameplayStatics::GetPlayerPawn(this, 0);
	if (!Player)
	{
		return;
	}

	// Ground-snap gate (2026-08-27, mirrors AODForestManager::Tick's identical
	// fix - see its own comment for the live-drive bug class this prevents):
	// don't dispatch any tile loads - and therefore don't finalize/place any
	// buildings - until the vehicle pawn has actually settled onto real
	// streamed terrain. Before this gate, a tile requested (and its async
	// parse completed) while the pawn was still parked at TeleportToSpawn's
	// fixed interim Z meant ComputeBuildingZOffset()'s ground trace ran
	// against terrain that either hadn't streamed in yet or was about to be
	// carved out from under it by AODTerrainManager::ApplyRoadCarve -
	// producing exactly the "grey slabs half-buried/tilted/floating" bug this
	// whole fix targets. Fails open for any pawn that isn't an
	// AODVehiclePawn, matching AODForestManager's identical gate.
	if (const AODVehiclePawn* VehiclePawn = Cast<AODVehiclePawn>(Player);
		VehiclePawn && !VehiclePawn->IsGroundResolved())
	{
		if (!bLoggedGroundSnapWait)
		{
			bLoggedGroundSnapWait = true;
			UE_LOG(LogTemp, Display,
				TEXT("ODBuildingManager: waiting for vehicle ground-snap before "
				     "streaming any building tiles"));
		}
		return;
	}
	if (!bLoggedGroundSnapConfirmed)
	{
		bLoggedGroundSnapConfirmed = true;
		UE_LOG(LogTemp, Display,
			TEXT("ODBuildingManager: ground-snap confirmed - beginning building "
			     "tile streaming"));
	}

	const FVector Loc = Player->GetActorLocation();
	// see PlayerZHint's declaration comment (ODBuildingManager.h) -
	// GroundAt()'s trace is relative to this instead of a hardcoded absolute Z.
	PlayerZHint = Loc.Z;
	const FIntPoint Center(FMath::FloorToInt(Loc.X / TileSizeCm),
	                       FMath::FloorToInt(Loc.Y / TileSizeCm));

	const int32 LoadReach = FMath::CeilToInt(SpawnRadius / TileSizeCm);
	const int32 DespawnReach = FMath::CeilToInt((SpawnRadius + DespawnMarginCm) / TileSizeCm);

	int32 Dispatched = 0;
	for (int32 Ring = 0; Ring <= LoadReach && Dispatched < MaxNewLoadsPerTick; ++Ring)
	{
		for (int32 dy = -Ring; dy <= Ring && Dispatched < MaxNewLoadsPerTick; ++dy)
		{
			for (int32 dx = -Ring; dx <= Ring && Dispatched < MaxNewLoadsPerTick; ++dx)
			{
				if (FMath::Max(FMath::Abs(dx), FMath::Abs(dy)) != Ring)
				{
					continue;
				}
				const FIntPoint Key = Center + FIntPoint(dx, dy);
				if (Tiles.Contains(Key) || PendingTiles.Contains(Key) || MissingTiles.Contains(Key))
				{
					continue;
				}
				RequestTileLoad(Key);
				++Dispatched;
			}
		}
	}

	TArray<FIntPoint> Gone;
	for (const auto& Pair : Tiles)
	{
		if (FMath::Max(FMath::Abs(Pair.Key.X - Center.X),
		               FMath::Abs(Pair.Key.Y - Center.Y)) > DespawnReach)
		{
			Gone.Add(Pair.Key);
		}
	}
	for (const FIntPoint& Key : Gone)
	{
		ReleaseTile(Key);
	}

	// Retry any building whose ground trace failed at its tile's finalize
	// time - see FinalizeTile()/ProcessPendingBuildings()'s own comments for
	// why this exists (the ground-snap gate above only proves the player's
	// own local ground is real, not that terrain has streamed under every
	// OTHER building tile inside SpawnRadius yet).
	ProcessPendingBuildings();

	if (const double Now = GetWorld()->GetTimeSeconds(); Now - LastDiagTime > 5.0)
	{
		LastDiagTime = Now;
		int32 NumBuildings = 0;
		int32 NumComponents = 0;
		for (const auto& Pair : Tiles)
		{
			NumBuildings += Pair.Value.NumBuildings;
			NumComponents += (Pair.Value.NearMesh ? 1 : 0) + (Pair.Value.FarMesh ? 1 : 0);
		}
		UE_LOG(LogTemp, Display,
			TEXT("ODBuildingManager: tiles=%d pending=%d missing=%d buildings=%d components=%d"),
			Tiles.Num(), PendingTiles.Num(), MissingTiles.Num(), NumBuildings, NumComponents);
		// Placement-fix QA summary (2026-08-27, temporary instrumentation for
		// the "grey slabs misplaced vertically" live-drive bug) - proves every
		// placed building went through a ground-snap-gated trace, how many
		// were rejected as phantom-surface/no-hit, and the resulting Z
		// correction range vs. the untouched baked height. minDelta/maxDelta
		// stay at the sentinel MAX/Lowest until at least one building has
		// been placed.
		UE_LOG(LogTemp, Display,
			TEXT("ODBuildingManager: placed=%d phantomRejects=%d noHitRejects=%d "
			     "pendingRetry=%d givenUp=%d minDeltaCm=%.1f maxDeltaCm=%.1f"),
			PlacedCount, PhantomRejectCount, NoHitRejectCount,
			PendingBuildings.Num(), GivenUpCount,
			PlacedCount > 0 ? MinPlacementDeltaCm : 0.0f,
			PlacedCount > 0 ? MaxPlacementDeltaCm : 0.0f);
	}
}
