// Far forest + street trees (realism eval T3, 2026-10-15) - AODForestManager
// members kept out of the 2.7k-line ODForestManager.cpp. Data:
// pipeline/build_far_forest.py's farforest_<area>_test.bin ('ODF1', per 2 km
// terrain tile: int32 tx, int32 ty, uint32 n, n * 7-byte records
// {uint16 cell = iy*100+ix, int8 jx, int8 jy (0.5 m), int16 z (dm), uint8 flags}).
// Flags: 0x80 = inside the near scatter's road band, 0x40 = street tree
// (low 6 bits = TreeMeshes species), else low bit = card type (0 needle,
// 1 broadleaf). Each tree cell stands FarCardsPerCell cards (2026-10-22): one
// card per 20 m cell was 25 trees/ha, open savanna beside the near scatter's
// ~330/ha - Taylor's "two lines of trees and nothing else". Streaming is
// distance-driven with a per-tick ms budget - no per-frame Dt anywhere, so it
// behaves the same at any frame rate.

#include "ODForestManager.h"
#include "ODTileSource.h"
#include "Components/HierarchicalInstancedStaticMeshComponent.h"
#include "Engine/StaticMesh.h"
#include "Materials/MaterialInstanceDynamic.h"
#include "MeshDescription.h"
#include "HAL/PlatformFileManager.h"
#include "Misc/Paths.h"
#include "StaticMeshAttributes.h"

namespace
{
constexpr double FarTileCm = 200000.0;   // build_far_forest.py TILE_M (terrain tile)
constexpr double FarCellCm = 2000.0;     // CELL_M
constexpr int32 FarCells = 100;          // CELLS
constexpr double FarChunkCm = 100000.0;  // streamed chunk, 2x2 per tile
constexpr int32 FarRecBytes = 7;
constexpr uint8 FarFlagBand = 0x80, FarFlagStreet = 0x40;
constexpr int32 FarDeciduousSpecies = 0;   // ODForestManager.cpp DeciduousSpeciesIndex
// 3 x ~8 m crowns per 400 m^2 ~ 40% canopy; the near trees plant the same
// (ForestTreesPerSqM = FarCardsPerCell / 400 m^2). Extra cards jitter inside
// the cell and follow the slope fitted through the neighbour cells' heights.
constexpr int32 FarCardsPerCell = 3;
// Extra cards also sink this x slope x offset, so a fit across a convex ridge
// buries them a little instead of floating them. Measured on the shipped
// terrain (analyze_forest_distribution.py --zcheck): floating past the 1.5 m
// sink 2.4% -> 0.6% (Summit County), 5.7% -> 1.2% (Deals Gap).
constexpr double FarExtraSink = 0.25;
// Past ~1.5 km (where cards drop to LOD1, MakeCardMesh) the extra cards cull
// and the primary card's LOD1 stands 1.7x wider for the cell's ~3 crowns:
// ~90% of the 5 km disc draws 1 card per cell, not 3 (4 GB VRAM pass).
constexpr int32 FarExtraCullCm = 150000;
constexpr float FarLod1Widen = 1.7f;
// Band cards hide per 200 m square, not per 1 km chunk: hiding a whole chunk
// once it touched SpawnRadius left the road corridor bare out to ~1.9 km
// ahead, since the near trees stop at SpawnRadius.
constexpr int32 FarBandSubCells = 10;                       // 200 m
constexpr int32 FarBandSubs = FarCells / 2 / FarBandSubCells;   // 5 per chunk side
}

bool AODForestManager::IsTreeCellAt(double WorldX, double WorldY, bool bRing) const
{
	const int32 Gx = FMath::FloorToInt(WorldX / FarCellCm), Gy = FMath::FloorToInt(WorldY / FarCellCm);
	const int32 R = bRing ? 1 : 0;
	for (int32 dy = -R; dy <= R; ++dy)
	{
		for (int32 dx = -R; dx <= R; ++dx)
		{
			const int32 X = Gx + dx, Y = Gy + dy;
			const FIntPoint Tile(FMath::FloorToInt(X / (double)FarCells), FMath::FloorToInt(Y / (double)FarCells));
			const TBitArray<>* Bits = FarTreeCells.Find(Tile);
			if (!Bits)
			{
				TBitArray<> New(false, FarCells * FarCells);
				TArray<uint8> Bytes;
				if (const TPair<int64, int32>* Recs = FarTileRecs.Find(Tile))
				{
					Bytes.SetNumUninitialized(Recs->Value * FarRecBytes);
					if (!FarFile->Seek(Recs->Key) || !FarFile->Read(Bytes.GetData(), Bytes.Num()))
					{
						Bytes.Reset();
					}
				}
				for (int32 o = 0; o + FarRecBytes <= Bytes.Num(); o += FarRecBytes)
				{
					const int32 Cell = Bytes[o] | Bytes[o + 1] << 8;
					if (!(Bytes[o + 6] & FarFlagStreet) && Cell < FarCells * FarCells)
					{
						New[Cell] = true;
					}
				}
				Bits = &FarTreeCells.Add(Tile, MoveTemp(New));
			}
			if ((*Bits)[(Y - Tile.Y * FarCells) * FarCells + (X - Tile.X * FarCells)])
			{
				return true;
			}
		}
	}
	return false;
}

bool AODForestManager::LoadFarForest()
{
	if (FarForestRadius <= 0.0f || !ElevationFilePrefix.StartsWith(TEXT("elev_")))
	{
		return false;
	}
	const TSharedPtr<const FFarIndex> Index = FarCache().Get(ElevationFilePrefix, &LoadFarIndex, TEXT("far forest index"));
	if (!Index.IsValid())
	{
		return false;
	}
	// Only the per-tile index stays in memory; BuildFarChunk reads a tile's
	// records on demand (Summit County's file is ~50 MB).
	FarFile.Reset(FPlatformFileManager::Get().GetPlatformFile().OpenRead(*Index->Path));
	if (!FarFile)
	{
		return false;
	}
	FarTileRecs = Index->TileRecs;   // ~1.2k entries
	const double T0 = FPlatformTime::Seconds();
	CardMeshes = {MakeCardMesh(0, 0), MakeCardMesh(1, 0)};   // flora_common: pine_needle, aspen_leaf
	UE_LOG(LogTemp, Display, TEXT("ODForestManager: far forest %s - %d tile(s), %lld record(s), %.1f MB on disk, card meshes %.1f ms"),
		*Index->Path, FarTileRecs.Num(), Index->NRecs, Index->Size / 1.0e6, (FPlatformTime::Seconds() - T0) * 1000.0);
	return true;
}

TSharedPtr<const AODForestManager::FFarIndex> AODForestManager::LoadFarIndex(const FString& ElevPrefix)
{
	if (!ElevPrefix.StartsWith(TEXT("elev_")))
	{
		return nullptr;
	}
	const double T0 = FPlatformTime::Seconds();
	const TSharedRef<FFarIndex> D = MakeShared<FFarIndex>();
	D->Path = ODTileSource::Fetch(FPaths::ProjectContentDir() / TEXT("OpenDrive/Masks")
		/ (TEXT("farforest_") + ElevPrefix.Mid(5) + TEXT(".bin")));
	TUniquePtr<IFileHandle> File(FPlatformFileManager::Get().GetPlatformFile().OpenRead(*D->Path));
	uint8 Head[12];
	if (!File || !File->Read(Head, 8) || FMemory::Memcmp(Head, "ODF1", 4) != 0)
	{
		return nullptr;
	}
	D->Size = File->Size();
	int64 Off = 8;
	const uint32 NTiles = *(const uint32*)(Head + 4);
	for (uint32 t = 0; t < NTiles && Off + 12 <= D->Size; ++t)
	{
		int32 Tx, Ty; uint32 N;
		File->Seek(Off);
		File->Read(Head, 12);
		FMemory::Memcpy(&Tx, Head, 4);
		FMemory::Memcpy(&Ty, Head + 4, 4);
		FMemory::Memcpy(&N, Head + 8, 4);
		Off += 12;
		if (Off + (int64)N * FarRecBytes > D->Size)
		{
			UE_LOG(LogTemp, Warning, TEXT("ODForestManager: %s truncated at tile %d"), *D->Path, t);
			break;
		}
		D->TileRecs.Add(FIntPoint(Tx, Ty), TPair<int64, int32>(Off, (int32)N));
		Off += (int64)N * FarRecBytes;
		D->NRecs += N;
	}
	UE_LOG(LogTemp, Display, TEXT("ODForestManager: far forest index %s - %d tile(s) in %.0f ms on a worker"),
		*D->Path, D->TileRecs.Num(), (FPlatformTime::Seconds() - T0) * 1000.0);
	if (D->TileRecs.Num() == 0)
	{
		return nullptr;
	}
	return D;
}

// Three vertical cards 60 deg apart plus a horizontal cap at 55% height (for
// views down from ridges), 100 cm unit, base at z=0, all UV'd onto one
// T_FoliageAtlas cell (flora_common.cell_uv_rect: 4x2 grid, 8% margin; UE V
// runs down the image, so the card's top is the cell's top). Up normals: the
// two-sided foliage shading then reads as one lit canopy mass, not
// light/dark card faces.
UStaticMesh* AODForestManager::MakeCardMesh(int32 AtlasRow, int32 AtlasCol)
{
	constexpr float M = 0.08f;
	const float U0 = (AtlasCol + M) / 4.0f, U1 = (AtlasCol + 1 - M) / 4.0f;
	const float VTop = (AtlasRow + M) / 2.0f, VBot = (AtlasRow + 1 - M) / 2.0f;

	// LOD0: 3 cards + cap (8 tris). LOD1: 2 crossed cards + cap (6 tris) once
	// a tree is ~10 px tall - the silhouette and top-down canopy both stay.
	FMeshDescription Descs[2];
	for (int32 Lod = 0; Lod < 2; ++Lod)
	{
		const int32 NumCards = Lod == 0 ? 3 : 2;
		const float Half = Lod == 0 ? 50.0f : 50.0f * FarLod1Widen;
		FMeshDescription& Desc = Descs[Lod];
		FStaticMeshAttributes Attr(Desc);
		Attr.Register();
		TVertexAttributesRef<FVector3f> Pos = Attr.GetVertexPositions();
		TVertexInstanceAttributesRef<FVector3f> Nrm = Attr.GetVertexInstanceNormals();
		TVertexInstanceAttributesRef<FVector3f> Tan = Attr.GetVertexInstanceTangents();
		TVertexInstanceAttributesRef<float> Sign = Attr.GetVertexInstanceBinormalSigns();
		TVertexInstanceAttributesRef<FVector2f> UVs = Attr.GetVertexInstanceUVs();
		const FPolygonGroupID PG = Desc.CreatePolygonGroup();
		Attr.GetPolygonGroupMaterialSlotNames()[PG] = TEXT("Foliage");
		const FVector2f T[4] = {{U0, VBot}, {U1, VBot}, {U1, VTop}, {U0, VTop}};
		auto Quad = [&](const FVector3f (&P)[4])
		{
			FVertexInstanceID Vi[4];
			for (int32 k = 0; k < 4; ++k)
			{
				const FVertexID V = Desc.CreateVertex();
				Pos[V] = P[k];
				Vi[k] = Desc.CreateVertexInstance(V);
				Nrm[Vi[k]] = FVector3f(0, 0, 1);
				Tan[Vi[k]] = FVector3f(1, 0, 0);
				Sign[Vi[k]] = 1.0f;
				UVs.Set(Vi[k], 0, T[k]);
			}
			const FVertexInstanceID A[3] = {Vi[0], Vi[1], Vi[2]};
			const FVertexInstanceID B[3] = {Vi[0], Vi[2], Vi[3]};
			Desc.CreateTriangle(PG, A);
			Desc.CreateTriangle(PG, B);
		};
		for (int32 i = 0; i < NumCards; ++i)
		{
			const float Ang = i * UE_PI / NumCards;
			const FVector3f D(FMath::Cos(Ang) * Half, FMath::Sin(Ang) * Half, 0.0f);
			const FVector3f Up(0, 0, 100);
			Quad({-D, D, D + Up, -D + Up});
		}
		Quad({{-Half, -Half, 55}, {Half, -Half, 55}, {Half, Half, 55}, {-Half, Half, 55}});
	}

	UStaticMesh* Mesh = NewObject<UStaticMesh>(this);
	Mesh->GetStaticMaterials().Add(FStaticMaterial(FoliageSeasonMID, TEXT("Foliage"), TEXT("Foliage")));
	UStaticMesh::FBuildMeshDescriptionsParams Params;
	Params.bBuildSimpleCollision = false;
	Params.bFastBuild = true;
	Mesh->BuildFromMeshDescriptions({&Descs[0], &Descs[1]}, Params);
	// sphere ~15 m for a needle card: 0.02 is ~1.5 km at the game's FOV. The
	// build's default (0.75^LOD) would drop to LOD1 at ~40 m.
	// ponytail: bounds come from LOD0, so the wider LOD1 can frustum-cull a
	// few px early at the screen edge; extend the bounds if that ever shows.
	Mesh->GetRenderData()->ScreenSize[1].Default = 0.02f;
	return Mesh;
}

void AODForestManager::BuildFarChunk(const FIntPoint& Key)
{
	FFarChunk& Chunk = FarChunks.Add(Key);
	const FIntPoint Tile(FMath::FloorToInt(Key.X / 2.0), FMath::FloorToInt(Key.Y / 2.0));
	const TPair<int64, int32>* Recs = FarTileRecs.Find(Tile);
	if (!Recs)
	{
		return;   // ocean / no tree cell / not this area's tile: stays an empty chunk
	}
	const int32 Ix0 = (Key.X - Tile.X * 2) * (FarCells / 2), Iy0 = (Key.Y - Tile.Y * 2) * (FarCells / 2);

	// batch key: mesh, band square (-1 = off-band), extra card (k > 0). Card meshes first, then street species.
	TMap<TTuple<UStaticMesh*, int32, bool>, TArray<FTransform>> Batches;
	TArray<uint8> Bytes;
	Bytes.SetNumUninitialized(Recs->Value * FarRecBytes);
	if (!FarFile->Seek(Recs->Key) || !FarFile->Read(Bytes.GetData(), Bytes.Num()))
	{
		return;
	}
	auto Decode = [&](const uint8* R)
	{
		const int32 Cell = R[0] | R[1] << 8;
		const int16 Zdm = (int16)(R[4] | R[5] << 8);
		return FVector(Tile.X * FarTileCm + (Cell % FarCells + 0.5) * FarCellCm + (int8)R[2] * 50.0,
		               Tile.Y * FarTileCm + (Cell / FarCells + 0.5) * FarCellCm + (int8)R[3] * 50.0, Zdm * 10.0);
	};
	// card bases by cell, for the extra cards' slope fit
	TArray<FVector> CellBase;
	CellBase.SetNumUninitialized(FarCells * FarCells);
	TBitArray<> HasCell(false, FarCells * FarCells);
	for (int32 r = 0; r < Recs->Value; ++r)
	{
		const uint8* R = Bytes.GetData() + r * FarRecBytes;
		const int32 Cell = R[0] | R[1] << 8;
		if (!(R[6] & FarFlagStreet) && Cell < FarCells * FarCells)
		{
			CellBase[Cell] = Decode(R);
			HasCell[Cell] = true;
		}
	}
	// dZ/dx (Axis 0) or dZ/dy at a cell: across the neighbour cells that
	// exist, one-sided at a gap, flat when the cell stands alone
	auto Slope = [&](int32 Cell, const FVector& B, int32 Axis)
	{
		const int32 I = Axis == 0 ? Cell % FarCells : Cell / FarCells;
		const int32 Step = Axis == 0 ? 1 : FarCells;
		const FVector& A = I > 0 && HasCell[Cell - Step] ? CellBase[Cell - Step] : B;
		const FVector& C = I < FarCells - 1 && HasCell[Cell + Step] ? CellBase[Cell + Step] : B;
		const double Run = C[Axis] - A[Axis];
		return FMath::Abs(Run) > 100.0 ? (C.Z - A.Z) / Run : 0.0;
	};

	const uint8* P = Bytes.GetData();
	for (int32 r = 0; r < Recs->Value; ++r, P += FarRecBytes)
	{
		const int32 Cell = P[0] | P[1] << 8;
		const int32 Ix = Cell % FarCells, Iy = Cell / FarCells;
		if (Ix < Ix0 || Ix >= Ix0 + FarCells / 2 || Iy < Iy0 || Iy >= Iy0 + FarCells / 2)
		{
			continue;
		}
		const uint8 Flags = P[6];
		const FVector Base = Decode(P);
		FRandomStream Rng((int32)HashCombine(GetTypeHash(Tile), (uint32)Cell));
		const FRotator Yaw(0.0, Rng.FRandRange(0.0, 360.0), 0.0);
		if (Flags & FarFlagStreet)
		{
			const int32 Species = Flags & 0x3F;
			UStaticMesh* Mesh = TreeMeshes.IsValidIndex(Species) ? TreeMeshes[Species].Get() : nullptr;
			if (Mesh)
			{
				Batches.FindOrAdd({Mesh, -1, false}).Add(FTransform(Yaw, Base - FVector(0, 0, 20), FVector(Rng.FRandRange(0.9, 1.25))));
				Chunk.Trunks.Add(Base);
			}
			continue;
		}
		const bool bNeedle = (Flags & 1) == 0;
		const bool bBand = (Flags & FarFlagBand) != 0;
		const int32 Sub = bBand ? (Iy - Iy0) / FarBandSubCells * FarBandSubs + (Ix - Ix0) / FarBandSubCells : -1;
		const double Gx = Slope(Cell, Base, 0), Gy = Slope(Cell, Base, 1);
		const FVector Center(Tile.X * FarTileCm + (Ix + 0.5) * FarCellCm, Tile.Y * FarTileCm + (Iy + 0.5) * FarCellCm, 0.0);
		UStaticMesh* Card = CardMeshes[bNeedle ? 0 : 1].Get();
		for (int32 k = 0; k < FarCardsPerCell; ++k)
		{
			FVector At = Base;
			if (k > 0)
			{
				At = Center + FVector(Rng.FRandRange(-0.45, 0.45), Rng.FRandRange(-0.45, 0.45), 0.0) * FarCellCm;
				At.Z = Base.Z + Gx * (At.X - Base.X) + Gy * (At.Y - Base.Y)
					- FarExtraSink * FMath::Sqrt(Gx * Gx + Gy * Gy) * FVector2D::Distance(FVector2D(At), FVector2D(Base));
			}
			const FRotator CardYaw = k == 0 ? Yaw : FRotator(0.0, Rng.FRandRange(0.0, 360.0), 0.0);
			const double W = bNeedle ? Rng.FRandRange(700.0, 1000.0) : Rng.FRandRange(1100.0, 1700.0);
			const double H = bNeedle ? Rng.FRandRange(1400.0, 2200.0) : Rng.FRandRange(1000.0, 1500.0);
			Batches.FindOrAdd({Card, Sub, k > 0}).Add(FTransform(CardYaw, At - FVector(0, 0, 150), FVector(W / 100.0, W / 100.0, H / 100.0)));
			if (!bBand)
			{
				Chunk.Trunks.Add(At);
			}
		}
	}

	for (TPair<TTuple<UStaticMesh*, int32, bool>, TArray<FTransform>>& B : Batches)
	{
		UStaticMesh* Mesh = B.Key.Get<0>();
		const int32 BandSub = B.Key.Get<1>();
		const bool bCard = CardMeshes.Contains(Mesh);
		auto* Hism = NewObject<UHierarchicalInstancedStaticMeshComponent>(this);
		Hism->SetupAttachment(RootComponent);
		Hism->SetStaticMesh(Mesh);
		UseSeasonFoliage(Hism);
		if (!bCard && TreeMeshes.IsValidIndex(FarDeciduousSpecies) && Mesh == TreeMeshes[FarDeciduousSpecies])
		{
			if (DeciduousBarkMID) { Hism->SetMaterial(0, DeciduousBarkMID); }
			if (DeciduousFoliageMID) { Hism->SetMaterial(1, DeciduousFoliageMID); }
		}
		Hism->SetCollisionEnabled(ECollisionEnabled::NoCollision);
		// cards: whole far radius (extras FarExtraCullCm); street trees: the near trees' 450/600 m
		const int32 CullEnd = !bCard ? 60000 : B.Key.Get<2>() ? FarExtraCullCm : (int32)FarForestRadius;
		Hism->SetCullDistances(bCard ? (int32)(CullEnd * 0.9f) : 45000, CullEnd);
		// ponytail: cards cast no shadow (100k+ instances in VSM); street trees do
		Hism->SetCastShadow(!bCard);
		Hism->WorldPositionOffsetDisableDistance = 10000;   // ODForestManager.cpp WpoDisableCm
		Hism->ShadowCacheInvalidationBehavior = EShadowCacheInvalidationBehavior::Rigid;
		Hism->SetAffectDynamicIndirectLighting(false);
		// cards stand past Lumen's ~200 m trace range and cast no shadow: keep
		// their ~350k instances out of the TLAS
		Hism->bVisibleInRayTracing = !bCard;
		Hism->RegisterComponent();
		Hism->AddInstances(B.Value, false, /*bWorldSpace*/false);   // actor-relative, same as BuildTile
		if (BandSub < 0)
		{
			Chunk.Hisms.Add(Hism);
			continue;
		}
		Hism->SetVisibility(false);   // UpdateFarForest shows it once the square is past the near scatter
		Chunk.BandHisms.Add(Hism);
		const FIntPoint Sq(BandSub % FarBandSubs, BandSub / FarBandSubs);
		Chunk.BandCenters.Add(FVector2D(Key) * FarChunkCm + (FVector2D(Sq) + 0.5) * (FarBandSubCells * FarCellCm));
	}
}

void AODForestManager::UpdateFarForest(const FVector& Loc)
{
	if (FarTileRecs.Num() == 0)
	{
		return;
	}
	const FIntPoint Center(FMath::FloorToInt(Loc.X / FarChunkCm), FMath::FloorToInt(Loc.Y / FarChunkCm));
	const int32 Reach = FMath::CeilToInt(FarForestRadius / FarChunkCm);
	const double Start = FPlatformTime::Seconds();
	bool bSpent = false;
	for (int32 Ring = 0; Ring <= Reach && !bSpent; ++Ring)
	{
		for (int32 dy = -Ring; dy <= Ring && !bSpent; ++dy)
		{
			for (int32 dx = -Ring; dx <= Ring && !bSpent; ++dx)
			{
				if (FMath::Max(FMath::Abs(dx), FMath::Abs(dy)) != Ring)
				{
					continue;
				}
				const FIntPoint Key = Center + FIntPoint(dx, dy);
				const FBox2D Box(FVector2D(Key) * FarChunkCm, FVector2D(Key + FIntPoint(1, 1)) * FarChunkCm);
				if (FarChunks.Contains(Key) || Box.ComputeSquaredDistanceToPoint(FVector2D(Loc)) > FMath::Square(FarForestRadius)
					|| !Vista.WantsTile(Key, FarChunkCm, 0.0, FMath::Max(Vista.FarCm, Vista.PanoFarCm)))
				{
					continue;
				}
				const double T0 = FPlatformTime::Seconds();
				BuildFarChunk(Key);
				const double Ms = (FPlatformTime::Seconds() - T0) * 1000.0;
				FarBuildMs += Ms;
				FarBuildMaxMs = FMath::Max(FarBuildMaxMs, Ms);
				++FarBuilt;
				bSpent = (FPlatformTime::Seconds() - Start) * 1000.0 > FarBuildMsBudget;
			}
		}
	}

	// release beyond the radius (+1 chunk hysteresis); a band square's cards
	// hide while its centre is inside the near scatter's SpawnRadius
	TArray<FIntPoint> Gone;
	for (TPair<FIntPoint, FFarChunk>& C : FarChunks)
	{
		const FBox2D Box(FVector2D(C.Key) * FarChunkCm, FVector2D(C.Key + FIntPoint(1, 1)) * FarChunkCm);
		const double D2 = Box.ComputeSquaredDistanceToPoint(FVector2D(Loc));
		if (D2 > FMath::Square(FarForestRadius + FarChunkCm))
		{
			Gone.Add(C.Key);
			continue;
		}
		for (int32 i = 0; i < C.Value.BandHisms.Num(); ++i)
		{
			UHierarchicalInstancedStaticMeshComponent* H = C.Value.BandHisms[i];
			const bool bShow = FVector2D::DistSquared(C.Value.BandCenters[i], FVector2D(Loc)) > FMath::Square(SpawnRadius);
			if (H && H->IsVisible() != bShow)
			{
				H->SetVisibility(bShow);
			}
		}
	}
	for (const FIntPoint& Key : Gone)
	{
		for (auto& H : FarChunks[Key].Hisms) { if (H) { H->DestroyComponent(); } }
		for (auto& H : FarChunks[Key].BandHisms) { if (H) { H->DestroyComponent(); } }
		FarChunks.Remove(Key);
	}

	if (const double Now = GetWorld()->GetTimeSeconds(); Now - FarLogTime > 5.0)
	{
		FarLogTime = Now;
		int32 NumCards = 0, NumStreet = 0, NumBandHidden = 0;
		for (const TPair<FIntPoint, FFarChunk>& C : FarChunks)
		{
			for (const auto& H : C.Value.Hisms)
			{
				if (H) { (CardMeshes.Contains(H->GetStaticMesh()) ? NumCards : NumStreet) += H->GetInstanceCount(); }
			}
			for (const auto& H : C.Value.BandHisms)
			{
				if (H) { (H->IsVisible() ? NumCards : NumBandHidden) += H->GetInstanceCount(); }
			}
		}
		UE_LOG(LogTemp, Display,
			TEXT("ODForestManager: far forest chunks=%d cards=%d (%d LOD0 tris) street=%d bandHidden=%d | built %d chunk(s) last 5s, %.2f ms total, %.2f ms max"),
			FarChunks.Num(), NumCards, NumCards * 8, NumStreet, NumBandHidden, FarBuilt, FarBuildMs, FarBuildMaxMs);
		FarBuilt = 0;
		FarBuildMs = FarBuildMaxMs = 0.0;
	}
}
