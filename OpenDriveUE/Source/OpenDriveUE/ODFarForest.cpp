// Far forest + street trees (realism eval T3, 2026-10-15) - AODForestManager
// members kept out of the 2.7k-line ODForestManager.cpp. Data:
// pipeline/build_far_forest.py's farforest_<area>_test.bin ('ODF1', per 2 km
// terrain tile: int32 tx, int32 ty, uint32 n, n * 7-byte records
// {uint16 cell = iy*100+ix, int8 jx, int8 jy (0.5 m), int16 z (dm), uint8 flags}).
// Flags: 0x80 = inside the near scatter's road band, 0x40 = street tree
// (low 6 bits = TreeMeshes species), else low bit = card type (0 needle,
// 1 broadleaf). Streaming is distance-driven with a per-tick ms budget - no
// per-frame Dt anywhere, so it behaves the same at any frame rate.

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
}

bool AODForestManager::LoadFarForest()
{
	if (FarForestRadius <= 0.0f || !ElevationFilePrefix.StartsWith(TEXT("elev_")))
	{
		return false;
	}
	const FString Path = ODTileSource::Fetch(FPaths::ProjectContentDir() / TEXT("OpenDrive/Masks")
		/ (TEXT("farforest_") + ElevationFilePrefix.Mid(5) + TEXT(".bin")));
	// Only the per-tile index stays in memory; BuildFarChunk reads a tile's
	// records on demand (Summit County's file is ~50 MB).
	FarFile.Reset(FPlatformFileManager::Get().GetPlatformFile().OpenRead(*Path));
	uint8 Head[12];
	if (!FarFile || !FarFile->Read(Head, 8) || FMemory::Memcmp(Head, "ODF1", 4) != 0)
	{
		FarFile.Reset();
		return false;
	}
	const int64 Size = FarFile->Size();
	int64 Off = 8, NRecs = 0;
	const uint32 NTiles = *(const uint32*)(Head + 4);
	for (uint32 t = 0; t < NTiles && Off + 12 <= Size; ++t)
	{
		int32 Tx, Ty; uint32 N;
		FarFile->Seek(Off);
		FarFile->Read(Head, 12);
		FMemory::Memcpy(&Tx, Head, 4);
		FMemory::Memcpy(&Ty, Head + 4, 4);
		FMemory::Memcpy(&N, Head + 8, 4);
		Off += 12;
		if (Off + (int64)N * FarRecBytes > Size)
		{
			UE_LOG(LogTemp, Warning, TEXT("ODForestManager: %s truncated at tile %d"), *Path, t);
			break;
		}
		FarTileRecs.Add(FIntPoint(Tx, Ty), TPair<int64, int32>(Off, (int32)N));
		Off += (int64)N * FarRecBytes;
		NRecs += N;
	}
	CardMeshes = {MakeCardMesh(0, 0), MakeCardMesh(1, 0)};   // flora_common: pine_needle, aspen_leaf
	UE_LOG(LogTemp, Display, TEXT("ODForestManager: far forest %s - %d tile(s), %lld record(s), %.1f MB on disk"),
		*Path, FarTileRecs.Num(), NRecs, Size / 1.0e6);
	return FarTileRecs.Num() > 0;
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

	FMeshDescription Desc;
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
	for (int32 i = 0; i < 3; ++i)
	{
		const float Ang = i * UE_PI / 3.0f;
		const FVector3f D(FMath::Cos(Ang) * 50.0f, FMath::Sin(Ang) * 50.0f, 0.0f);
		const FVector3f Up(0, 0, 100);
		Quad({-D, D, D + Up, -D + Up});
	}
	Quad({{-50, -50, 55}, {50, -50, 55}, {50, 50, 55}, {-50, 50, 55}});

	UStaticMesh* Mesh = NewObject<UStaticMesh>(this);
	Mesh->GetStaticMaterials().Add(FStaticMaterial(FoliageSeasonMID, TEXT("Foliage"), TEXT("Foliage")));
	UStaticMesh::FBuildMeshDescriptionsParams Params;
	Params.bBuildSimpleCollision = false;
	Params.bFastBuild = true;
	Mesh->BuildFromMeshDescriptions({&Desc}, Params);
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

	// batch key: mesh, band. Card meshes first, then street species.
	TMap<TPair<UStaticMesh*, bool>, TArray<FTransform>> Batches;
	TArray<uint8> Bytes;
	Bytes.SetNumUninitialized(Recs->Value * FarRecBytes);
	if (!FarFile->Seek(Recs->Key) || !FarFile->Read(Bytes.GetData(), Bytes.Num()))
	{
		return;
	}
	const uint8* P = Bytes.GetData();
	for (int32 r = 0; r < Recs->Value; ++r, P += FarRecBytes)
	{
		const int32 Cell = P[0] | P[1] << 8;
		const int32 Ix = Cell % FarCells, Iy = Cell / FarCells;
		if (Ix < Ix0 || Ix >= Ix0 + FarCells / 2 || Iy < Iy0 || Iy >= Iy0 + FarCells / 2)
		{
			continue;
		}
		const int16 Zdm = (int16)(P[4] | P[5] << 8);
		const uint8 Flags = P[6];
		const FVector Base(Tile.X * FarTileCm + (Ix + 0.5) * FarCellCm + (int8)P[2] * 50.0,
		                   Tile.Y * FarTileCm + (Iy + 0.5) * FarCellCm + (int8)P[3] * 50.0, Zdm * 10.0);
		FRandomStream Rng((int32)HashCombine(GetTypeHash(Tile), (uint32)Cell));
		const FRotator Yaw(0.0, Rng.FRandRange(0.0, 360.0), 0.0);
		if (Flags & FarFlagStreet)
		{
			const int32 Species = Flags & 0x3F;
			UStaticMesh* Mesh = TreeMeshes.IsValidIndex(Species) ? TreeMeshes[Species].Get() : nullptr;
			if (Mesh)
			{
				Batches.FindOrAdd({Mesh, false}).Add(FTransform(Yaw, Base - FVector(0, 0, 20), FVector(Rng.FRandRange(0.9, 1.25))));
				Chunk.Trunks.Add(Base);
			}
			continue;
		}
		const bool bNeedle = (Flags & 1) == 0;
		const double W = bNeedle ? Rng.FRandRange(700.0, 1000.0) : Rng.FRandRange(1100.0, 1700.0);
		const double H = bNeedle ? Rng.FRandRange(1400.0, 2200.0) : Rng.FRandRange(1000.0, 1500.0);
		const bool bBand = (Flags & FarFlagBand) != 0;
		Batches.FindOrAdd({CardMeshes[bNeedle ? 0 : 1].Get(), bBand})
			.Add(FTransform(Yaw, Base - FVector(0, 0, 150), FVector(W / 100.0, W / 100.0, H / 100.0)));
		if (!bBand)
		{
			Chunk.Trunks.Add(Base);
		}
	}

	for (TPair<TPair<UStaticMesh*, bool>, TArray<FTransform>>& B : Batches)
	{
		UStaticMesh* Mesh = B.Key.Key;
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
		// cards: whole far radius; street trees: the near trees' 450/600 m
		Hism->SetCullDistances(bCard ? (int32)(FarForestRadius * 0.9f) : 45000, bCard ? (int32)FarForestRadius : 60000);
		// ponytail: cards cast no shadow (100k+ instances in VSM); street trees do
		Hism->SetCastShadow(!bCard);
		Hism->WorldPositionOffsetDisableDistance = 10000;   // ODForestManager.cpp WpoDisableCm
		Hism->ShadowCacheInvalidationBehavior = EShadowCacheInvalidationBehavior::Rigid;
		Hism->SetAffectDynamicIndirectLighting(false);
		Hism->RegisterComponent();
		Hism->AddInstances(B.Value, false, /*bWorldSpace*/false);   // actor-relative, same as BuildTile
		(B.Key.Value ? Chunk.BandHisms : Chunk.Hisms).Add(Hism);
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

	// release beyond the radius (+1 chunk hysteresis); band cards hide while
	// the near scatter (SpawnRadius) reaches into the chunk
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
		const bool bBand = D2 > FMath::Square(SpawnRadius + 5000.0);
		if (bBand != C.Value.bBandVisible)
		{
			C.Value.bBandVisible = bBand;
			for (auto& H : C.Value.BandHisms)
			{
				if (H) { H->SetVisibility(bBand); }
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
				if (H) { (C.Value.bBandVisible ? NumCards : NumBandHidden) += H->GetInstanceCount(); }
			}
		}
		UE_LOG(LogTemp, Display,
			TEXT("ODForestManager: far forest chunks=%d cards=%d (%d tris) street=%d bandHidden=%d | built %d chunk(s) last 5s, %.2f ms total, %.2f ms max"),
			FarChunks.Num(), NumCards, NumCards * 8, NumStreet, NumBandHidden, FarBuilt, FarBuildMs, FarBuildMaxMs);
		FarBuilt = 0;
		FarBuildMs = FarBuildMaxMs = 0.0;
	}
}
