#include "ODTerrainManager.h"
#include "ODTickMeter.h"
#include "UObject/Package.h" // ODVisualStyle.h uses UPackage (non-unity builds)
#include "ODVisualStyle.h"

#include "ODBinSidecarUtil.h"
#include "ODTileSource.h"
#include "ODBakeLink.h"
#include "ODForestManager.h" // EODBiome only, for BiomeGroundTintTable below - no calls into AODForestManager itself
#include "Async/Async.h"
#include "Components/DynamicMeshComponent.h"
#include "DynamicMesh/DynamicMesh3.h"
#include "DynamicMesh/DynamicMeshAttributeSet.h"
#include "DynamicMesh/DynamicMeshOverlay.h"
#include "DynamicMesh/MeshNormals.h"
#include "Engine/Texture2D.h"
#include "Engine/World.h"
#include "ImageCore.h"
#include "ImageCoreUtils.h"
#include "ImageUtils.h"
#include "Dom/JsonObject.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"
#include "Kismet/GameplayStatics.h"
#include "Materials/MaterialInterface.h"
#include "Materials/MaterialInstanceDynamic.h"
#include "HAL/PlatformFileManager.h"
#include "Misc/FileHelper.h"
#include "TimerManager.h"
#include "Misc/Paths.h"
#include "Misc/AutomationTest.h"
#include "Misc/Compression.h"
#include "Tasks/Task.h"

using namespace UE::Geometry;

static void FillTileMesh(FDynamicMesh3& Mesh, const FParsedTerrainTile& Parsed);
static void TagOutsideOutline(FDynamicMesh3& Mesh, const FVector2D& Origin, const FString& MaskPath);

namespace
{
	// One area's rasters + horizon mesh (2026-10-22): decoded/built on
	// workers from PrewarmArea (the menu kicks it at pin pick), then shared by
	// the vista's terrain manager and the level's after LoadMap - each used to
	// spend 2-3 s of game thread on the same three PNGs (livetest7/8).
	const TCHAR* const AreaTexKinds[] = {TEXT("sat"), TEXT("pave"), TEXT("dem")};
	constexpr int32 NumAreaTex = UE_ARRAY_COUNT(AreaTexKinds);
	struct FAreaPng
	{
		TSharedPtr<FJsonObject> Meta;
		FImage Image;   // empty: meta but no readable PNG
	};
	struct FAreaHorizon
	{
		FDynamicMesh3 Mesh;
		FVector2D Origin;   // FillTileMesh's local origin
		int32 NumVerts = 0, NumTris = 0;
	};
	struct FAreaCache
	{
		FString Key;   // terrain prefix + ODBakeLink status version: a drawn area's re-bake misses
		UE::Tasks::TTask<TSharedPtr<FAreaPng>> Png[NumAreaTex];
		bool bTexDone[NumAreaTex] = {};
		TSharedPtr<FJsonObject> Meta[NumAreaTex];
		UTexture2D* Tex[NumAreaTex] = {};   // rooted while cached
		UE::Tasks::TTask<TSharedPtr<const FAreaHorizon>> Horizon;
	};
	// ponytail: one area (summit_county ~330 MB of rasters); key by area if hopping back and forth shows up
	FAreaCache GAreaCache;

	FString AreaMaskBase(const FString& TerrainPrefix, const TCHAR* Kind)
	{
		return FPaths::ProjectContentDir() / TEXT("OpenDrive/Masks/") + Kind + TEXT("_") + TerrainPrefix.Mid(8);
	}

	// Game thread: the decoded PNG as a transient, rooted texture - the tail
	// of FImageUtils::ImportFileAsTexture2D, with the sampler settings set
	// before the one UpdateResource instead of after a first one.
	void CreateAreaTexture(int32 K)
	{
		FAreaCache& C = GAreaCache;
		C.bTexDone[K] = true;
		const TSharedPtr<FAreaPng> Png = C.Png[K].GetResult();
		if (!Png.IsValid())
		{
			return;
		}
		C.Meta[K] = Png->Meta;
		const FImage& Img = Png->Image;
		if (Img.SizeX <= 0)
		{
			return;
		}
		const double T0 = FPlatformTime::Seconds();
		ERawImageFormat::Type RawFormat;
		const EPixelFormat PixelFormat = FImageCoreUtils::GetPixelFormatForRawImageFormat(Img.Format, &RawFormat);
		UTexture2D* Tex = UTexture2D::CreateTransient(Img.SizeX, Img.SizeY, PixelFormat);
		if (!Tex)
		{
			return;
		}
		Tex->bNotOfflineProcessed = true;
		Tex->SRGB = true;              // linear values, sRGB-encoded (matches the Color sampler)
		Tex->AddressX = TA_Clamp;      // border texels = no data / no pave outside the extent
		Tex->AddressY = TA_Clamp;
		FByteBulkData& Bulk = Tex->GetPlatformData()->Mips[0].BulkData;
		FImageCore::CopyImage(Img, FImageView(Bulk.Lock(LOCK_READ_WRITE), Img.SizeX, Img.SizeY, 1, RawFormat, Img.GammaSpace));
		Bulk.Unlock();
		Tex->UpdateResource();
		Tex->AddToRoot();
		C.Tex[K] = Tex;
		UE_LOG(LogTemp, Display, TEXT("ODTerrainManager: %s texture %dx%d created in %.0f ms on the game thread"),
			AreaTexKinds[K], Img.SizeX, Img.SizeY, (FPlatformTime::Seconds() - T0) * 1000.0);
	}
}

// ---------------------------------------------------------------------
// Runtime terrain carve (2026-08-26, Arc 7 rescope): a straight port of
// pipeline/build_terrain_mesh_tiles.py's carve_grid_m(), applied per-vertex
// to an already-parsed tile's Verts instead of to a pre-triangulation
// elevation grid - see ApplyRoadCarve below for the actual port. Values
// MUST stay numerically identical (in cm, the pipeline's own meters * 100)
// to the pipeline's own constants, or a runtime-carved tile would visibly
// disagree with an offline-baked one sitting right next to it. Comments
// here intentionally stay short; see carve_grid_m's own docstring/inline
// comments in the pipeline file for the full history of each number.
static constexpr float CarveInnerCm = 5000.0f;         // TERRAIN_CARVE_INNER_M (50 m)
static constexpr float CarveOuterCm = 15000.0f;         // TERRAIN_CARVE_OUTER_M (150 m)
static constexpr float CarveDropCm = 15.0f;             // TERRAIN_CARVE_DROP_M (0.15 m)
static constexpr float CarveCutInnerCm = 2500.0f;       // TERRAIN_CARVE_CUT_INNER_M (25 m)
static constexpr float CarveCutOuterCm = 7500.0f;       // TERRAIN_CARVE_CUT_OUTER_M (75 m)
// HARD_CLAMP_RADIUS_M = ROAD_HALF_WIDTH+SKIRT_EXTRA (7.5 m) - the pipeline's
// own literal footprint radius. Kept as the FLOOR of ApplyRoadCarve's dynamic
// clamp radius (see that function), not the radius itself any more: 7.5 m is
// narrower than this manager's own terrain grid spacing on plenty of tiles
// (near tiles run ~8-16 m/vert past the near-grid center, far/coarse tiles
// ~100 m/vert), so a road can thread BETWEEN two clamped vertices and the
// interpolated triangle between them still pokes up through the pavement -
// found 2026-08-26 via Taylor's live drive ("green terrain patches poke up
// through/above the road surface"). HardClampMarginCm (the fixed 15 cm drop
// bias) became the tunable AODTerrainManager::RoadCarveClampMarginCm
// (default 40 cm) for the same reason - see that UPROPERTY's comment.
static constexpr float HardClampRadiusCm = 750.0f;
// SKIRT_EXTRA (3 m) - per-station footprint radius is now the station's own
// HalfWidthCm (ODS2 sidecar, lanes-tag width 2026-09-06) + this skirt, which
// reproduces HardClampRadiusCm exactly for a classic 4.5 m half-width station
// and mirrors the pipeline's own clamp_r = half_width + SKIRT_EXTRA.
static constexpr float SkirtExtraCm = 300.0f;

// How far past a tile's own bounds to query the station spatial hash -
// comfortably past CarveOuterCm's 150 m taper so no vertex near a tile edge
// can miss a station sitting just outside it.
static constexpr float StationQueryMarginCm = 16000.0f; // 160 m

// Grade-aware switchback-vs-ordinary-grade filter for ApplyRoadCarve's
// hard-clamp min-over-radius search (2026-08-27 follow-up fix - see that
// function's comment). Ported straight from pipeline/road_mesh_common.py's
// CONFLICT_MAX_GRADE/CONFLICT_MIN_GAP_M: two stations this close in Z for
// their XY separation are plausibly the SAME graded road continuing up/
// downhill (not flagged); a bigger gap than any real road profile could
// produce is a genuine different pass (a switchback/hairpin) and DOES need
// to win the clamp. Kept numerically identical to the pipeline's own
// constants for the same "don't let the two carves disagree" reason
// CarveInnerCm/CarveOuterCm/etc. already state above.
static constexpr float ConflictMaxGrade = 0.15f;
static constexpr float ConflictMinGapCm = 100.0f;   // CONFLICT_MIN_GAP_M (1 m)

// ---------------------------------------------------------------------
// Runtime per-vertex ground tint (2026-08-26, terrain biome tint task) -
// biome + elevation + road-distance + deterministic noise, computed in the
// SAME async parse-thread pass as ApplyRoadCarve above (see ApplyBiomeTint).
// Zero new baked data: the biome signal reads AODForestManager's own
// point-classified sidecar (pipeline/classify_biomes.py + export_biome_bin.py),
// the elevation/road/noise signals are pure runtime math against data this
// manager already has loaded (carved Verts, StationHash).

// Retuned 2026-09-03 for summit_county (Phase 3a ground-visual overhaul) -
// these were still Green Valley's own plausible elevation range (0-2200 m
// absolute, ElevLowCm/ElevHighCm as small POSITIVE offsets from that area's
// near-sea-level ORIGIN_HEIGHT_M) and had gone stale for the new active area
// (pipeline/test_areas.py ACTIVE_AREA flipped to summit_county 2026-09-01,
// ORIGIN_HEIGHT_M=3435.3 m - see that constant's comment). At summit_county's
// origin, world Z=0 sits at 3435 m ASL, so the OLD 60000-170000 cm band
// (meant to read as "600-1700 m absolute") instead read as "4035-5135 m
// absolute" here - entirely above every real point in the area (peak
// elevation is Quandary's 4348 m), so the elevation-driven low/high tint
// bias silently never engaged at all; every tile read the same flat
// mid-blend regardless of real elevation. Real terrain here spans roughly
// 2364-4053 m (ELEV_PLAUSIBLE_M's sampled range) i.e. UE Z from about
// -107000 to +62000 cm - bands below sized against valley floors (Frisco/
// Dillon ~2700 m) through low peaks, distinct from the higher treeline/
// snow-line bands unreal/apply_ground_material.py's own TREELINE_LOW_CM/
// SNOW_LOW_CM use (those model where trees/snow physically stop; this pair
// is a softer/lower-elevation "valley green -> foothill grey" cosmetic
// gradient feeding the vertex TINT only, not the material's texture-layer
// picks). A different AREA_NAME would want these re-tuned again, same as
// every other area-specific constant in this file (e.g. SEA_LEVEL_Z_CM in
// unreal/apply_ground_material.py, itself already ORIGIN_HEIGHT_M-derived).
// 2026-09-04 RETUNE #2 (§7.15 pale-background-hills follow-up): the first
// retune (below) fixed where full grey LANDS but left ElevLowCm untouched at
// -70000 (~2735 m) - essentially valley-floor level for summit_county (Frisco/
// Dillon/Silverthorne all sit ~2700-2800 m). That meant the ElevT/ElevS
// smoothstep started climbing from the moment terrain rose above the valley
// floor at all, long before real treeline: at 3268 m (a measured Silverthorne
// background-hill sample, well below apply_ground_material.py's own
// TREELINE_LOW_M=3350) the old band already gave ElevS~=0.33, i.e. already a
// third of the way toward HighBias's flat neutral grey (0.40,0.40,0.40) -
// a diagnostic render (flat_w(R)/treeline_t(G) wired straight to base color)
// confirmed a pale/grey hillside pixel at flat_w~=1 (fully flat, vegetated
// branch) and treeline_t~=0 (nowhere near the MATERIAL's own treeline gate),
// proving this vertex-tint elevation bias - not the material's slope/treeline
// texture layer - was the one painting it grey. Raised to match
// TREELINE_LOW_M exactly so this cosmetic gradient can't out-run the
// material's own below-treeline look: full "rich/dark low green" bias now
// holds everywhere below real treeline, only starting to greigh out right
// where the material itself starts blending toward scree.
// 2026-10-15 (realism eval T1): ElevLowCm/ElevHighCm are now per area -
// FODGroundBands::ElevLowCm/ElevHighCm from groundbands_<area>_test.json
// (pipeline/build_ground_bands.py: treeline low .. +765 m, climate-derived),
// passed into ApplyBiomeTint (summit_county's file reproduces the values the
// comments here describe, give or take the climate fit).
// 2026-09-04 RETUNE #1 (§7.15 background-slope follow-up): was 40000 (~3835 m),
// which put FULL grey bias well BELOW unreal/apply_ground_material.py's own
// TREELINE_HIGH_M=3650 - i.e. this vertex-tint elevation gradient was already
// greying out real below-treeline forest slopes (target look: warm green/
// brown below ~3400 m) before the material's own texture-layer treeline
// logic had even started blending toward scree. Raised so full grey doesn't
// land until just above the real treeline instead of a third of the way up
// the valley wall.
static const FLinearColor ElevLowBias(0.02f, 0.10f, 0.02f);
static const FLinearColor ElevHighBias(0.40f, 0.40f, 0.40f);

// Road-verge distance band - reuses the SAME station hash ApplyRoadCarve
// already queries, but at a much tighter radius: this is a pure cosmetic
// "dusty shoulder" cue, not a geometry taper (contrast CarveOuterCm's 150 m).
// 2026-08-26 tightened from 10 m/20 m/full-strength after Taylor's junction
// screenshots: a junction is a dense CLUSTER of stations from several roads,
// so per-station verge bands union into one huge flat grey apron swallowing
// the whole pad area ("two blobs" - the true asphalt pad reading as a dark
// patch inside a giant dirt disc). A narrower band that never fully replaces
// the ground tint keeps the dusty-shoulder cue without the junction bloom.
static constexpr float VergeInnerCm = 800.0f;   // 8 m - skirt edge, max verge blend
static constexpr float VergeOuterCm = 1300.0f;  // 13 m - faded back to ground tint
static constexpr float VergeMaxBlend = 0.55f;   // never fully hide the biome tint
static const FLinearColor RoadVergeTint(0.30f, 0.27f, 0.22f); // dusty packed dirt/gravel

// Deterministic position-hash noise - two octaves for "large-scale
// patchiness" (Taylor's spec), not per-vertex jitter, hence scales far above
// vertex spacing (NEAR_GRID_N=257 over a 2000 m tile is ~8 m spacing).
static constexpr float NoiseScaleFineCm = 5000.0f;    // 50 m
static constexpr float NoiseScaleCoarseCm = 20000.0f; // 200 m
static constexpr float NoiseStrength = 0.35f;         // how far toward the "dry variant" full noise pushes
static const FLinearColor DryVariantMul(1.25f, 1.05f, 0.75f); // pushes warmer/drier, roughly desaturating green

// Biome blend radius - same order of magnitude as
// AODForestManager::BiomeBlendRadiusCm's own 300 m default, derived here
// from FBiomeSpatialHash::CellSizeCm rather than a second independent magic
// number.
static constexpr float BiomeBlendRadiusCm = FBiomeSpatialHash::CellSizeCm * 1.5f; // 300 m

// 2026-09-04 (§7.15 background-slope follow-up): classify_biomes.py only
// samples points ALONG the road corridor (every SAMPLE_STRIDE-th road
// station, see that script's module docstring: "the player only experiences
// biome variety near the road, so only that imagery needs fetching/
// classifying") - but the terrain mesh itself extends far past the road,
// out to BACKGROUND_BBOX_SNWE (pipeline/test_areas.py), tens of km wide, for
// the background hills/peaks a vantage actually sees. A vertex out there has
// no biome sample anywhere near it, so the "nothing within
// BiomeBlendRadiusCm" branch below used to fall back to the single nearest
// road-corridor sample point OUTRIGHT, however far away that was - often a
// rocky roadcut or switchback km away, not the real forest cover the distant
// slope shows in-game. That made ground color a function of "distance to the
// nearest sampled road point" rather than of the actual terrain, i.e. the
// exact "pale far from the road, correct near it" symptom. Past this cap,
// a real-but-remote sample is no longer trusted as representative and the
// loop instead keeps the (already-initialized, warm) default GroundTint -
// same default a tile with literally zero biome candidates already used -
// so distant background terrain reads as consistent forest-floor green
// instead of inheriting whatever's nearest along a road that may be km away.
static constexpr float FarBiomeFallbackCm = 100000.0f; // 1 km
// (2026-09-25, V1: classify_biomes.py now also samples a 300 m grid over the
// whole terrain bbox, so this fallback only covers tile-edge query-box gaps.)

// V2 aspect/moisture tint modulation (sec 7.35) - palette is a first pass,
// needs Taylor's live eyes. Strengths are the blend at |Northness|=1 /
// Moisture=1; real samples mostly sit well inside that (see the sec 7.35
// histograms).
static const FLinearColor DrySlopeTint(0.30f, 0.27f, 0.13f);   // dry grass/sage
static constexpr float AspectDryStrength = 0.45f;
static constexpr float AspectShadeStrength = 0.30f;
static const FLinearColor MoistMeadowTint(0.09f, 0.22f, 0.06f); // wet meadow green
static constexpr float MoistureStrength = 0.40f;

namespace
{
/** Terrain's OWN ground-tint table, indexed by EODBiome (ODForestManager.h) -
 *  a PARALLEL copy to that module's BiomeRecipeTable, not a shared one: that
 *  table encodes tree species/density weights for scatter, this one encodes
 *  ground COLOR - same enum, different payload per module, same "keep the
 *  enum header-only, values local to whichever .cpp actually uses them"
 *  convention ODForestManager.cpp's own BiomeRecipeTable comment describes. */
struct FBiomeGroundTint { FLinearColor Tint; };
const FBiomeGroundTint BiomeGroundTintTable[] = {
	/* DeciduousForest */ { FLinearColor(0.06f, 0.16f, 0.05f) },
	/* EvergreenForest */ { FLinearColor(0.04f, 0.14f, 0.07f) },
	/* Chaparral        */ { FLinearColor(0.42f, 0.38f, 0.18f) }, // dry tan/olive
	/* DesertScrub      */ { FLinearColor(0.55f, 0.42f, 0.22f) }, // ochre/sand
	/* PlainsGrassland  */ { FLinearColor(0.35f, 0.36f, 0.14f) },
	/* Wetland          */ { FLinearColor(0.10f, 0.20f, 0.10f) },
	/* AlpineBare       */ { FLinearColor(0.35f, 0.36f, 0.35f) }, // grey-green rock
	/* ArcticTundra     */ { FLinearColor(0.40f, 0.40f, 0.38f) },
	/* Farmland         */ { FLinearColor(0.30f, 0.28f, 0.14f) },
	/* Savanna          */ { FLinearColor(0.45f, 0.40f, 0.18f) },
	/* Rainforest       */ { FLinearColor(0.04f, 0.15f, 0.05f) }, // richer green
	/* CoastalDune      */ { FLinearColor(0.50f, 0.46f, 0.32f) },
	/* CanyonBadlands   */ { FLinearColor(0.48f, 0.34f, 0.22f) },
	/* RockyFoothill    */ { FLinearColor(0.28f, 0.30f, 0.24f) }, // grey-green transition
	/* TropicalCoast    */ { FLinearColor(0.55f, 0.50f, 0.36f) }, // pale coral sand
	/* LavaField        */ { FLinearColor(0.05f, 0.05f, 0.055f) }, // fresh basalt, near black
};
static_assert(UE_ARRAY_COUNT(BiomeGroundTintTable) == (uint8)EODBiome::LavaField + 1,
	"BiomeGroundTintTable must have one entry per EODBiome value, in enum order");

// HashLattice/ValueNoise2D moved to ODBinSidecarUtil.h (2026-09-25) -
// AODForestManager's V5 aspen mask reuses the same noise.
}

AODTerrainManager::AODTerrainManager()
{
	PrimaryActorTick.bCanEverTick = true;
	RootComponent = CreateDefaultSubobject<USceneComponent>(TEXT("Root"));
}

void AODTerrainManager::BeginPlay()
{
	Super::BeginPlay();

	// Placeholder stylized ground material (unreal/apply_ground_material.py) -
	// slope-blended grass/rock plus a sand/beach blend near sea level, no
	// PM_Asphalt so 1.4's road/off-road grip split reads this as off-road,
	// which is correct (this is the ground beside the road, not the
	// driving surface). LoadObject failing is a silent no-op, same
	// null-safety convention as AODRoadManager::RoadMat.
	TerrainMat = ODVisualStyle::Pick(LoadObject<UMaterialInterface>(
		nullptr, TEXT("/Game/OpenDrive/Materials/M_GroundStylized.M_GroundStylized")));
	UE_LOG(LogTemp, Display, TEXT("ODTerrainManager: TerrainMat load %s"),
		TerrainMat ? TEXT("OK") : TEXT("FAILED (terrain will use engine default material)"));

	// Seasons feature (2026-09-07): one shared MID for every tile to render
	// with instead of the raw asset - see TerrainMID's header comment.
	if (TerrainMat)
	{
		TerrainMID = UMaterialInstanceDynamic::Create(TerrainMat, this);
		LoadGroundBands();
		if (ODVisualStyle::IsRealistic())
		{
			SatTex = BindAreaTexture(TEXT("sat"), TEXT("Sat"));
			PaveTex = BindAreaTexture(TEXT("pave"), TEXT("Pave"));
			DemTex = BindAreaTexture(TEXT("dem"), TEXT("Dem"));
		}
	}

	// Runtime terrain carve (2026-08-26): load the road-station sidecar once
	// up front, before any tile load can be dispatched from Tick - see
	// LoadRoadStationSidecar/ApplyRoadCarve.
	LoadRoadStationSidecar();

	// Runtime per-vertex ground tint (2026-08-26): load the same
	// point-classified biome sidecar AODForestManager's BlendAt() reads, into
	// this manager's OWN spatial hash - see LoadBiomeSidecar/ApplyBiomeTint.
	LoadBiomeSidecar();
	// drawn areas only use it (outline masks / ODBakeLink outline); a missing
	// asset is the same silent no-op as TerrainMat (2026-10-21)
	UnsyncedMat = LoadObject<UMaterialInterface>(nullptr, TEXT("/Game/OpenDrive/Materials/M_ODUnsynced.M_ODUnsynced"));
	LoadHorizon();

	// Diagnostic-only override (2026-08-25): lets a headless test bisect
	// how many resident near-fidelity (collision-enabled) tiles trigger the
	// open Chaos/UE5.8 collision-registration bug without a recompile per
	// step - see AODRoadManager's matching -odspawnradius= override.
	float OverrideRadius = 0.0f;
	if (FParse::Value(FCommandLine::Get(), TEXT("odcollisionradius="), OverrideRadius))
	{
		CollisionRadius = OverrideRadius;
		UE_LOG(LogTemp, Display, TEXT("ODTerrainManager: odcollisionradius override -> %.0f"), CollisionRadius);
	}

	// Temporary QA override (2026-08-26) - forces bVerifyCarveGuarantee on
	// without a recompile, same convention as -odcollisionradius= above. See
	// that UPROPERTY's comment for what it does.
	if (FParse::Param(FCommandLine::Get(), TEXT("odverifycarve")))
	{
		bVerifyCarveGuarantee = true;
		UE_LOG(LogTemp, Display, TEXT("ODTerrainManager: odverifycarve override -> carve-guarantee spot-check ON"));
	}
}

void AODTerrainManager::EndPlay(const EEndPlayReason::Type EndPlayReason)
{
	const int32 NumInFlight = PendingCoarseTiles.Num() + PendingNearTiles.Num();
	if (NumInFlight > 0)
	{
		UE_LOG(LogTemp, Display,
			TEXT("ODTerrainManager: EndPlay with %d tile load(s) still in flight - "
			     "their completions will no-op harmlessly when they land"),
			NumInFlight);
	}
	Super::EndPlay(EndPlayReason);
}

bool AODTerrainManager::ParseTerrainTileFile(const FString& Path, FParsedTerrainTile& Out)
{
	TArray<uint8> Bin;
	return FFileHelper::LoadFileToArray(Bin, *Path) && ParseTerrainTileBytes(Bin, Out);
}

// ODT2 (2026-10-21, pipeline map-area-picker session): same float32 verts and
// triangles as ODT1, but UVs are dropped (derived from x/y below) and the
// verts+indices payload is one zlib stream, indices uint16 when flags bit0 is
// set. ~30% of the ODT1 size on the Tail of the Dragon tiles. Generation id
// sits at bytes 4..20 in both, so nothing downstream can tell them apart.
static bool ParseTerrainTileOdt2(const uint8* Cur, const uint8* End, FParsedTerrainTile& Out)
{
	// flags(1) V(4) T(4) u0/v0/uv_size float64(24) Z(4)
	if (End - Cur < 37)
	{
		return false;
	}
	const bool bU16 = (Cur[0] & 1) != 0;
	Cur += 1;
	const uint32 VCount = ReadU32LE(Cur); Cur += 4;
	const uint32 TCount = ReadU32LE(Cur); Cur += 4;
	double U0, V0, UvSize;
	FMemory::Memcpy(&U0, Cur, 8); Cur += 8;
	FMemory::Memcpy(&V0, Cur, 8); Cur += 8;
	FMemory::Memcpy(&UvSize, Cur, 8); Cur += 8;
	const uint32 ZBytes = ReadU32LE(Cur); Cur += 4;

	const int64 IdxBytes = bU16 ? 2 : 4;
	const int64 RawBytes = (int64)VCount * 12 + (int64)TCount * 3 * IdxBytes;
	// zlib can't expand past ~1032:1, so a bigger claim is a corrupt header -
	// reject it before allocating rather than let a bad V/T ask for GBs
	if (End - Cur < (int64)ZBytes || ZBytes == 0 || RawBytes > MAX_int32
		|| RawBytes > (int64)ZBytes * 1032 || !(UvSize > 0.0))
	{
		return false;
	}
	TArray<uint8> Raw;
	Raw.SetNumUninitialized((int32)RawBytes);
	// UncompressMemory itself fails unless the stream inflates to exactly
	// RawBytes; an empty tile (V=T=0, legal in ODT1 too) has nothing to inflate
	if (RawBytes > 0 && !FCompression::UncompressMemory(NAME_Zlib, Raw.GetData(), RawBytes, Cur, ZBytes))
	{
		return false;
	}

	const uint8* VertsBase = Raw.GetData();
	const uint8* TrisBase = VertsBase + (int64)VCount * 12;
	Out.Verts.SetNumUninitialized(VCount * 3);
	Out.Uvs.SetNumUninitialized(VCount * 2);
	for (uint32 v = 0; v < VCount; ++v)
	{
		const float X = ReadF32LE(VertsBase + v * 12 + 0);
		const float Y = ReadF32LE(VertsBase + v * 12 + 4);
		Out.Verts[v * 3 + 0] = X;
		Out.Verts[v * 3 + 1] = Y;
		Out.Verts[v * 3 + 2] = ReadF32LE(VertsBase + v * 12 + 8);
		// in double, then stored as float - the writer verified this matches
		// its ODT1 UVs (within 2e-5) before choosing ODT2
		Out.Uvs[v * 2 + 0] = (float)(((double)X - U0) / UvSize);
		Out.Uvs[v * 2 + 1] = (float)(((double)Y - V0) / UvSize);
	}
	Out.Tris.SetNumUninitialized(TCount * 3);
	for (uint32 i = 0; i < TCount * 3; ++i)
	{
		Out.Tris[i] = bU16 ? ((uint32)TrisBase[i * 2] | (uint32)TrisBase[i * 2 + 1] << 8)
			: ReadU32LE(TrisBase + (int64)i * 4);
	}
	return true;
}

bool AODTerrainManager::ParseTerrainTileBytes(const TArray<uint8>& Bin, FParsedTerrainTile& Out)
{
	if (Bin.Num() < 4 + GenerationIdBytes + 9
		|| Bin[0] != 'O' || Bin[1] != 'D' || Bin[2] != 'T' || (Bin[3] != '1' && Bin[3] != '2'))
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

	if (Remaining() < 1)
	{
		return false;
	}
	Out.bHasCollision = (*Cur != 0);
	Cur += 1;

	if (Bin[3] == '2')
	{
		return ParseTerrainTileOdt2(Cur, End, Out);
	}

	if (Remaining() < 8)
	{
		return false;
	}
	const uint32 VCount = ReadU32LE(Cur); Cur += 4;
	const uint32 TCount = ReadU32LE(Cur); Cur += 4;
	const int64 NeedBytes = (int64)VCount * 12 + (int64)VCount * 8 + (int64)TCount * 12;
	if (Remaining() < NeedBytes)
	{
		return false;
	}

	const uint8* VertsBase = Cur;
	const uint8* UvBase = Cur + (int64)VCount * 12;
	const uint8* TrisBase = Cur + (int64)VCount * 12 + (int64)VCount * 8;

	Out.Verts.SetNumUninitialized(VCount * 3);
	for (uint32 v = 0; v < VCount; ++v)
	{
		Out.Verts[v * 3 + 0] = ReadF32LE(VertsBase + v * 12 + 0);
		Out.Verts[v * 3 + 1] = ReadF32LE(VertsBase + v * 12 + 4);
		Out.Verts[v * 3 + 2] = ReadF32LE(VertsBase + v * 12 + 8);
	}
	Out.Uvs.SetNumUninitialized(VCount * 2);
	for (uint32 v = 0; v < VCount; ++v)
	{
		Out.Uvs[v * 2 + 0] = ReadF32LE(UvBase + v * 8 + 0);
		Out.Uvs[v * 2 + 1] = ReadF32LE(UvBase + v * 8 + 4);
	}
	Out.Tris.SetNumUninitialized(TCount * 3);
	for (uint32 t = 0; t < TCount; ++t)
	{
		Out.Tris[t * 3 + 0] = ReadU32LE(TrisBase + t * 12 + 0);
		Out.Tris[t * 3 + 1] = ReadU32LE(TrisBase + t * 12 + 4);
		Out.Tris[t * 3 + 2] = ReadU32LE(TrisBase + t * 12 + 8);
	}
	return true;
}

// ParseRoadStationSidecarFile moved to ODBinSidecarUtil.h (2026-08-26,
// carve-clamp/road-exclusion rescope) - AODForestManager::LoadRoadStations
// now calls the same shared free function.

bool AODTerrainManager::ParseBiomeSidecarFile(const FString& Path, TArray<FBiomeSample>& OutPoints)
{
	// 'ODB1', uint32 LE N, N * (float32 X_cm, float32 Y_cm, uint8 Biome,
	// uint8 ForestWeight255) - pipeline/classify_biomes.py +
	// export_biome_bin.py, the SAME format/file AODForestManager::
	// LoadBiomePoints reads (see that function). No generation-id field in
	// this header (unlike ODS1/ODT1) - export_biome_bin.py never added one.
	// 'ODB2' (2026-09-25, sec 7.35 V2) appends uint8 Northness255, uint8
	// Moisture255, uint16 ElevM to each record; ODB1 still parses (neutral
	// defaults).
	TArray<uint8> Bin;
	if (!FFileHelper::LoadFileToArray(Bin, *Path) || Bin.Num() < 8
		|| Bin[0] != 'O' || Bin[1] != 'D' || Bin[2] != 'B' || (Bin[3] != '1' && Bin[3] != '2'))
	{
		return false;
	}
	const bool bV2 = Bin[3] == '2';
	const int32 N = (int32)ReadU32LE(Bin.GetData() + 4);
	const int32 RecordBytes = bV2 ? 14 : 10; // X_cm, Y_cm, Biome, ForestWeight255 [, Northness255, Moisture255, ElevM]
	if (N <= 0 || Bin.Num() < 8 + (int64)N * RecordBytes)
	{
		return false;
	}
	OutPoints.SetNum(N); // not Uninitialized - ODB1 relies on the V2 field defaults
	const uint8* P = Bin.GetData() + 8;
	for (int32 i = 0; i < N; ++i, P += RecordBytes)
	{
		OutPoints[i].X = ReadF32LE(P);
		OutPoints[i].Y = ReadF32LE(P + 4);
		OutPoints[i].Biome = P[8];
		// P[9] (ForestWeight255) is AODForestManager's own scatter-density
		// signal - not read here, this manager only wants the biome id.
		if (bV2)
		{
			OutPoints[i].Northness = P[10] / 127.5f - 1.0f;
			OutPoints[i].Moisture = P[11] / 255.0f;
		}
	}
	return true;
}

void AODTerrainManager::LoadRoadStationSidecar()
{
	// Same "<AREA_NAME>" the pipeline itself uses (build_road_stations.py's
	// "area" field) - TerrainTilePrefix is "terrain_<AREA_NAME>_test"
	// (matches the "_coarse_" fallback's own Mid(8) split just above), so
	// strip the "terrain_" prefix AND the "_test" suffix the tile-file
	// naming convention adds on top of the pipeline's own bare AREA_NAME -
	// confirmed against the real exported file
	// (Content/OpenDrive/Masks/roadstations_green_valley.bin, no "_test").
	if (!TerrainTilePrefix.StartsWith(TEXT("terrain_")))
	{
		UE_LOG(LogTemp, Warning,
			TEXT("ODTerrainManager: TerrainTilePrefix %s doesn't start with \"terrain_\" - "
			     "can't derive the road-station sidecar's area name, runtime carve disabled"),
			*TerrainTilePrefix);
		return;
	}
	FString Area = TerrainTilePrefix.Mid(8);
	Area.RemoveFromEnd(TEXT("_test"));

	const FString Path = ODTileSource::Fetch(FPaths::ProjectContentDir() / TEXT("OpenDrive/Masks")
		/ FString::Printf(TEXT("roadstations_%s.bin"), *Area));

	TArray<FRoadStation> Loaded;
	FString GenId;
	if (!ParseRoadStationSidecarFile(Path, Loaded, GenId))
	{
		// Missing/corrupt sidecar is not fatal - terrain still streams and
		// builds fine, it just carries whatever carve the offline bake
		// already applied (see this manager's class comment) and none of
		// the runtime kind. Logged once, here, since LoadRoadStationSidecar
		// only ever runs once (BeginPlay).
		UE_LOG(LogTemp, Warning,
			TEXT("ODTerrainManager: road-station sidecar %s missing or corrupt - "
			     "runtime terrain carve disabled (terrain will use its offline-baked carve only)"),
			*Path);
		return;
	}

	if (Vista.bActive)
	{
		// menu vista (2026-10-05): keep only stations a wanted tile can carve
		Loaded.RemoveAll([this](const FRoadStation& St)
		{
			return !Vista.WantsPoint(FVector2D(St.X, St.Y), 1.25 * TileSizeCm);
		});
	}
	TSharedRef<FStationSpatialHash, ESPMode::ThreadSafe> Hash = MakeShared<FStationSpatialHash, ESPMode::ThreadSafe>();
	const int32 NumStations = Loaded.Num();
	Hash->Build(MoveTemp(Loaded));
	StationHash = Hash;
	UE_LOG(LogTemp, Display,
		TEXT("ODTerrainManager: loaded %d road station(s) from %s (gen %s) for runtime terrain carve"),
		NumStations, *Path, *GenId);
}

bool AODTerrainManager::PrewarmArea(const FString& TerrainPrefix)
{
	check(IsInGameThread());
	if (!TerrainPrefix.StartsWith(TEXT("terrain_")))
	{
		return true;
	}
	FAreaCache& C = GAreaCache;
	const FString Key = FString::Printf(TEXT("%s@%u"), *TerrainPrefix, ODBakeLink::StatusVersion());
	if (C.Key != Key)
	{
		for (UTexture2D*& Tex : C.Tex)
		{
			if (Tex)
			{
				Tex->RemoveFromRoot();   // managers still showing it hold their own reference
			}
		}
		C = FAreaCache();
		C.Key = Key;
		for (int32 K = 0; K < NumAreaTex; ++K)
		{
			const FString Base = AreaMaskBase(TerrainPrefix, AreaTexKinds[K]);
			C.Png[K] = UE::Tasks::Launch(UE_SOURCE_LOCATION, [Base]() -> TSharedPtr<FAreaPng>
			{
				FString Json;
				TSharedPtr<FJsonObject> Meta;
				if (!FFileHelper::LoadFileToString(Json, *ODTileSource::Fetch(Base + TEXT(".json")))
					|| !FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Json), Meta) || !Meta.IsValid())
				{
					return nullptr;
				}
				TSharedPtr<FAreaPng> Png = MakeShared<FAreaPng>();
				Png->Meta = Meta;
				FImageUtils::LoadImage(*ODTileSource::Fetch(Base + TEXT(".png")), Png->Image);
				return Png;
			});
		}
		const FString HorizonPath = FPaths::ProjectContentDir() / TEXT("OpenDrive/Masks")
			/ (TEXT("horizon_") + TerrainPrefix.Mid(8) + TEXT(".bin"));
		C.Horizon = UE::Tasks::Launch(UE_SOURCE_LOCATION, [HorizonPath]() -> TSharedPtr<const FAreaHorizon>
		{
			FString ReadPath;
			FParsedTerrainTile Parsed;
			if (ODTileSource::Resolve(HorizonPath, ReadPath) != EODTileSource::Ready || !ParseTerrainTileFile(ReadPath, Parsed)
				|| Parsed.Verts.Num() < 3)
			{
				return nullptr;
			}
			TSharedPtr<FAreaHorizon> H = MakeShared<FAreaHorizon>();
			FillTileMesh(H->Mesh, Parsed);
			H->Origin = FVector2D(Parsed.Verts[0], Parsed.Verts[1]);
			H->NumVerts = Parsed.Verts.Num() / 3;
			H->NumTris = Parsed.Tris.Num() / 3;
			return H;
		});
	}
	// one texture per call: pave alone is a 233 MB copy (8192x7115 BGRA8)
	for (int32 K = 0; K < NumAreaTex; ++K)
	{
		if (!C.bTexDone[K])
		{
			if (C.Png[K].IsCompleted())
			{
				CreateAreaTexture(K);
			}
			return false;
		}
	}
	return C.Horizon.IsCompleted();
}

UTexture2D* AODTerrainManager::BindAreaTexture(const TCHAR* Kind, const TCHAR* Param)
{
	if (!TerrainTilePrefix.StartsWith(TEXT("terrain_")))
	{
		return nullptr;
	}
	const FString Base = AreaMaskBase(TerrainTilePrefix, Kind);
	// the menu's pin pick normally built these already (PrewarmArea); else wait here
	const double T0 = FPlatformTime::Seconds();
	PrewarmArea(TerrainTilePrefix);
	int32 K = 0;
	while (K < NumAreaTex - 1 && FCString::Strcmp(Kind, AreaTexKinds[K]) != 0)
	{
		++K;
	}
	if (!GAreaCache.bTexDone[K])
	{
		CreateAreaTexture(K);
	}
	const TSharedPtr<FJsonObject> Meta = GAreaCache.Meta[K];
	if (!Meta.IsValid())
	{
		UE_LOG(LogTemp, Display, TEXT("ODTerrainManager: no %s.json - %sAmount stays 0"), *Base, Param);
		return nullptr;
	}
	UTexture2D* Tex = GAreaCache.Tex[K];
	if (!Tex)
	{
		UE_LOG(LogTemp, Warning, TEXT("ODTerrainManager: %s.png missing or unreadable - %sAmount stays 0"), *Base, Param);
		return nullptr;
	}
	const FString P(Param);
	const FVector Off = GetActorLocation();   // tiles sit at actor-relative positions
	TerrainMID->SetTextureParameterValue(*(P + TEXT("Tex")), Tex);
	TerrainMID->SetDoubleVectorParameterValue(*(P + TEXT("Origin")),
		FVector4(Meta->GetNumberField(TEXT("x_min_cm")) + Off.X, Meta->GetNumberField(TEXT("y_min_cm")) + Off.Y, 0.0, 0.0));
	TerrainMID->SetVectorParameterValue(*(P + TEXT("SizeCm")),
		FLinearColor(Meta->GetNumberField(TEXT("size_x_cm")), Meta->GetNumberField(TEXT("size_y_cm")), 0.0f, 0.0f));
	TerrainMID->SetScalarParameterValue(*(P + TEXT("Amount")), 1.0f);
	UE_LOG(LogTemp, Display, TEXT("ODTerrainManager: %s %dx%d bound, waited %.0f ms (%s)"),
		*Base, Tex->GetSizeX(), Tex->GetSizeY(), (FPlatformTime::Seconds() - T0) * 1000.0, *Meta->GetStringField(TEXT("attribution")));
	return Tex;
}

void AODTerrainManager::LoadGroundBands()
{
	if (!TerrainTilePrefix.StartsWith(TEXT("terrain_")))
	{
		return;
	}
	const FString Path = ODTileSource::Fetch(FPaths::ProjectContentDir() / TEXT("OpenDrive/Masks/groundbands_")
		+ TerrainTilePrefix.Mid(8) + TEXT(".json"));
	FString Json;
	TSharedPtr<FJsonObject> O;
	if (!FFileHelper::LoadFileToString(Json, *Path)
		|| !FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Json), O) || !O.IsValid())
	{
		UE_LOG(LogTemp, Warning, TEXT("ODTerrainManager: %s missing - no treeline/snow/beach bands (run pipeline/build_ground_bands.py)"), *Path);
	}
	else
	{
		FODGroundBands& B = GroundBands;
		const auto Read = [&O](const TCHAR* Key, float& Out) { double V; if (O->TryGetNumberField(Key, V)) { Out = (float)V; } };
		Read(TEXT("sea_z_cm"), B.SeaZCm);
		Read(TEXT("treeline_low_cm"), B.TreelineLowCm);
		Read(TEXT("treeline_high_cm"), B.TreelineHighCm);
		Read(TEXT("elev_low_cm"), B.ElevLowCm);
		Read(TEXT("elev_high_cm"), B.ElevHighCm);
		Read(TEXT("snow_summer_low_cm"), B.SnowSummerLowCm);
		Read(TEXT("snow_summer_high_cm"), B.SnowSummerHighCm);
		Read(TEXT("snow_winter_low_cm"), B.SnowWinterLowCm);
		Read(TEXT("snow_winter_high_cm"), B.SnowWinterHighCm);
		Read(TEXT("low_green_bias"), B.LowGreenBias);
	}
	// tiles sit at actor-relative positions; the material reads world Z
	const float Dz = (float)GetActorLocation().Z;
	FODGroundBands& W = GroundBandsWorld;
	W = GroundBands;
	for (float* F : { &W.SeaZCm, &W.TreelineLowCm, &W.TreelineHighCm, &W.ElevLowCm, &W.ElevHighCm,
		&W.SnowSummerLowCm, &W.SnowSummerHighCm, &W.SnowWinterLowCm, &W.SnowWinterHighCm })
	{
		*F += Dz;
	}
	TerrainMID->SetScalarParameterValue(TEXT("SeaLevelZCm"), W.SeaZCm);
	TerrainMID->SetScalarParameterValue(TEXT("TreelineLowZCm"), W.TreelineLowCm);
	TerrainMID->SetScalarParameterValue(TEXT("TreelineHighZCm"), W.TreelineHighCm);
	TerrainMID->SetScalarParameterValue(TEXT("SnowLineLowZCm"), W.SnowSummerLowCm);
	TerrainMID->SetScalarParameterValue(TEXT("SnowLineHighZCm"), W.SnowSummerHighCm);
	UE_LOG(LogTemp, Display, TEXT("ODTerrainManager: ground bands sea=%.0f treeline=%.0f..%.0f snow summer=%.0f..%.0f winter=%.0f..%.0f (world cm)"),
		W.SeaZCm, W.TreelineLowCm, W.TreelineHighCm, W.SnowSummerLowCm, W.SnowSummerHighCm, W.SnowWinterLowCm, W.SnowWinterHighCm);
}

void AODTerrainManager::LoadBiomeSidecar()
{
	// Same "<AREA_NAME>" derivation LoadRoadStationSidecar uses above, but
	// the filename convention here KEEPS the "_test" suffix
	// (unreal/build_openworld_test_level.py sets AODForestManager's own
	// BiomeFilePrefix to f"biome_{AREA_NAME}_test", written by
	// pipeline/export_biome_bin.py) - unlike roadstations_{area}.bin, which
	// drops it.
	if (!TerrainTilePrefix.StartsWith(TEXT("terrain_")))
	{
		return; // already warned by LoadRoadStationSidecar
	}
	FString Area = TerrainTilePrefix.Mid(8);
	Area.RemoveFromEnd(TEXT("_test"));

	const FString Path = ODTileSource::Fetch(FPaths::ProjectContentDir() / TEXT("OpenDrive/Masks")
		/ FString::Printf(TEXT("biome_%s_test.bin"), *Area));

	TArray<FBiomeSample> Loaded;
	if (!ParseBiomeSidecarFile(Path, Loaded))
	{
		// Missing/corrupt sidecar is not fatal - ApplyBiomeTint still runs
		// off the elevation/road/noise signals alone, just without a real
		// per-point biome color to blend (see that function's fallback).
		UE_LOG(LogTemp, Warning,
			TEXT("ODTerrainManager: biome sidecar %s missing or corrupt - "
			     "runtime ground tint will use elevation/road/noise signals only"),
			*Path);
		return;
	}

	TSharedRef<FBiomeSpatialHash, ESPMode::ThreadSafe> Hash = MakeShared<FBiomeSpatialHash, ESPMode::ThreadSafe>();
	const int32 NumPoints = Loaded.Num();
	Hash->Build(MoveTemp(Loaded));
	BiomeHash = Hash;
	UE_LOG(LogTemp, Display,
		TEXT("ODTerrainManager: loaded %d biome point(s) from %s for runtime ground tint"),
		NumPoints, *Path);
}

// Statistically-impossible-vertex safety net (2026-08-27) - see the header
// declaration's comment for the full "why" (phantom-shard investigation, no
// data-level defect found in a live offline audit, ships as a safety net
// regardless). Threshold is deliberately generous (default call site passes
// 20000 cm / 200 m) - Green Valley's own tiles legitimately swing 500+ m
// across a single tile in rugged terrain, so this must only catch a vertex
// that disagrees with LITERALLY EVERY neighbor it has, not merely a steep
// one.
void AODTerrainManager::RepairOutlierVertices(FParsedTerrainTile& Tile, const FIntPoint& Key, float ThresholdCm,
	int32& OutNumRepaired, float& OutMaxDeltaCm)
{
	OutNumRepaired = 0;
	OutMaxDeltaCm = 0.0f;
	const uint32 VCount = Tile.Verts.Num() / 3;
	const uint32 TCount = Tile.Tris.Num() / 3;
	if (VCount == 0 || TCount == 0)
	{
		return;
	}

	// Mesh-adjacency neighbor lists, same "walk Tile.Tris" approach
	// ApplyRoadCarve's own MaxAdjEdgeCm uses - the ODT1 vertex order isn't
	// documented as row-major (see VerifyCarveGuarantee's comment), so this
	// is the only ordering-agnostic way to ask "what's actually next to this
	// vertex in the triangulation".
	TArray<TArray<uint32>> Neighbors;
	Neighbors.SetNum(VCount);
	for (uint32 t = 0; t < TCount; ++t)
	{
		const uint32 TriIdx[3] = { Tile.Tris[t * 3 + 0], Tile.Tris[t * 3 + 1], Tile.Tris[t * 3 + 2] };
		for (int32 e = 0; e < 3; ++e)
		{
			const uint32 A = TriIdx[e];
			const uint32 B = TriIdx[(e + 1) % 3];
			Neighbors[A].AddUnique(B);
			Neighbors[B].AddUnique(A);
		}
	}

	// Raw Z snapshot (2026-10-14): repairing in place used to let an
	// already-repaired (or still-bad) neighbor feed the next vertex's mean -
	// Oahu's offshore Terrarium voids (-32768 m clusters) produced "repaired"
	// Zs of -3 km. Detect and repair against the raw values, median not
	// mean, so one bad neighbor can't drag the result.
	TArray<float> RawZ;
	RawZ.SetNumUninitialized(VCount);
	for (uint32 v = 0; v < VCount; ++v)
	{
		RawZ[v] = Tile.Verts[v * 3 + 2];
	}

	TArray<float> NbrZ;
	for (uint32 v = 0; v < VCount; ++v)
	{
		const TArray<uint32>& Nbrs = Neighbors[v];
		if (Nbrs.Num() == 0)
		{
			continue; // isolated vertex (shouldn't happen for a real tile) - nothing to compare against
		}

		const float Z = RawZ[v];
		float MinAbsDelta = TNumericLimits<float>::Max();
		NbrZ.Reset();
		for (uint32 N : Nbrs)
		{
			NbrZ.Add(RawZ[N]);
			MinAbsDelta = FMath::Min(MinAbsDelta, FMath::Abs(Z - RawZ[N]));
		}

		if (MinAbsDelta <= ThresholdCm)
		{
			continue; // agrees with at least one neighbor within threshold - not an outlier
		}

		NbrZ.Sort();
		const float RepairedZ = NbrZ[NbrZ.Num() / 2];
		// Verbose (2026-10-14): one line per vertex was 3,666 Warnings on
		// one Oahu load; the per-tile summary in the caller stays a Warning.
		if (UE_LOG_ACTIVE(LogTemp, Verbose))
		{
			FString NeighborZList;
			for (float NZ : NbrZ)
			{
				NeighborZList += FString::Printf(TEXT("%.1f,"), NZ / 100.0f);
			}
			UE_LOG(LogTemp, Verbose,
				TEXT("ODTerrainManager: outlier vertex repaired - tile (%d,%d) vert=%u rawZ=%.1fm ")
				TEXT("neighborZs_m=[%s] repairedZ=%.1fm (min neighbor delta %.1fm > threshold %.1fm)"),
				Key.X, Key.Y, v, Z / 100.0f, *NeighborZList, RepairedZ / 100.0f,
				MinAbsDelta / 100.0f, ThresholdCm / 100.0f);
		}

		Tile.Verts[v * 3 + 2] = RepairedZ;
		++OutNumRepaired;
		OutMaxDeltaCm = FMath::Max(OutMaxDeltaCm, MinAbsDelta);
	}
}

void AODTerrainManager::ApplyRoadCarve(FParsedTerrainTile& Tile, const FIntPoint& Key, float TileSizeCm,
	const FStationSpatialHash& Hash, ETileFidelity Fidelity, bool bBuildWalls, float WallThresholdCm,
	float ClampMarginCm, int32& OutVertsTouched, float& OutMaxDropCm, float& OutMaxLiftCm,
	int32& OutHardClampedVerts)
{
	OutVertsTouched = 0;
	OutMaxDropCm = 0.0f;
	OutMaxLiftCm = 0.0f;
	OutHardClampedVerts = 0;
	const uint32 VCount = Tile.Verts.Num() / 3;
	if (VCount == 0 || Hash.Stations.Num() == 0)
	{
		return;
	}

	// Per-vertex dynamic clamp radius (2026-08-26 fix - see HardClampRadiusCm's
	// comment above for the "road threads between two clamped verts" bug this
	// closes). FIRST CUT of this fix assumed every tile was a uniform N x N
	// grid and derived one tile-wide spacing from sqrt(VCount) - wrong: FAR
	// tiles (FAR_GRID_N=21) really are a plain uniform grid, but NEAR tiles
	// are martini-simplified (pymartini, MAX_ERROR_NEAR_M=0.3 m -
	// pipeline/build_terrain_mesh_tiles.py) BEFORE export, i.e. an ADAPTIVE
	// triangulation with wildly non-uniform edge lengths (dense right at the
	// carve zone, sparse - and therefore with LONG edges - out on flat
	// ground far from any road). A single sqrt(VCount)-derived spacing badly
	// underestimates the real gap between two mesh-adjacent vertices out in
	// the sparse regions, which is exactly where -odverifycarve caught real
	// violations (surface still poking above station elevation - margin)
	// even after that first cut.
	//
	// The actually-correct, triangulation-shape-agnostic fix: measure each
	// vertex's own worst-case edge length to whatever it's ACTUALLY
	// mesh-adjacent to (from Tile.Tris, whatever the triangulation looks
	// like) and use THAT as its local spacing - this is precisely "could the
	// segment between this vertex and an adjacent one span the road", which
	// is the guarantee being asked for, without needing to assume anything
	// about grid regularity at all.
	TArray<float> MaxAdjEdgeCm;
	MaxAdjEdgeCm.SetNumZeroed(VCount);
	{
		const uint32 AdjTCount = Tile.Tris.Num() / 3;
		for (uint32 t = 0; t < AdjTCount; ++t)
		{
			const uint32 TriIdx[3] = { Tile.Tris[t * 3 + 0], Tile.Tris[t * 3 + 1], Tile.Tris[t * 3 + 2] };
			for (int32 e = 0; e < 3; ++e)
			{
				const uint32 A = TriIdx[e];
				const uint32 B = TriIdx[(e + 1) % 3];
				const float Dx = Tile.Verts[A * 3 + 0] - Tile.Verts[B * 3 + 0];
				const float Dy = Tile.Verts[A * 3 + 1] - Tile.Verts[B * 3 + 1];
				const float EdgeLenCm = FMath::Sqrt(Dx * Dx + Dy * Dy);
				MaxAdjEdgeCm[A] = FMath::Max(MaxAdjEdgeCm[A], EdgeLenCm);
				MaxAdjEdgeCm[B] = FMath::Max(MaxAdjEdgeCm[B], EdgeLenCm);
			}
		}
	}
	// A vertex with no triangle at all (shouldn't happen for a real tile, but
	// cheap to guard) falls back to HardClampRadiusCm alone via the Max()
	// below (MaxAdjEdgeCm[v] stays 0).
	//
	// 2026-08-27: bumped from 0.75x to 1.5x (i.e. more than a full adjacent
	// edge length, not half of one) after -odverifycarve still turned up a
	// handful of violations at 0.75x - martini's adaptive simplification
	// produces the occasional thin/obtuse sliver triangle where a station
	// sitting inside the triangle can be farther from ITS nearest vertex than
	// half that vertex's longest adjacent edge (the 0.75x bound assumed a
	// roughly-regular triangle shape, which near tiles don't always have).
	// 1.5x cleared every remaining sample in the same spot-check. On
	// far/coarse tiles (uniform ~100 m grid) this makes for a visually wide
	// clamp trench - a flat, several-hundred-meter shelf pinned to road
	// height either side of the route - which would look wrong up close, but
	// coarse tiles only ever render at 2 km+ (see VisualRadius/
	// CollisionRadius), where a flat verge reads as ordinary distant terrain
	// and is a strict visual improvement over grass floating above the
	// pavement.

	// Query once per tile, not per vertex - carve_grid_m's own chunked
	// nearest-station search is the pipeline's answer to the same "don't let
	// (grid points x stations) blow up" problem; querying the spatial hash
	// once up front and then brute-forcing nearest-of-candidates per vertex
	// is this port's equivalent bound (candidates are already localized to
	// this tile's own neighborhood, not the whole route).
	const float MinX = Key.X * TileSizeCm - StationQueryMarginCm;
	const float MaxX = (Key.X + 1) * TileSizeCm + StationQueryMarginCm;
	const float MinY = Key.Y * TileSizeCm - StationQueryMarginCm;
	const float MaxY = (Key.Y + 1) * TileSizeCm + StationQueryMarginCm;

	TArray<int32> Candidates;
	Hash.QueryBox(MinX, MinY, MaxX, MaxY, Candidates);
	if (Candidates.Num() == 0)
	{
		return; // no road anywhere near this tile - nothing to carve
	}

	// Only kept when bBuildWalls is on (Phase 2, default off) - see the
	// wall-ribbon pass below.
	TArray<bool> HardCut;
	TArray<float> RawZ;
	if (bBuildWalls)
	{
		HardCut.SetNumZeroed(VCount);
		RawZ.SetNumUninitialized(VCount);
	}

	for (uint32 v = 0; v < VCount; ++v)
	{
		const float X = Tile.Verts[v * 3 + 0];
		const float Y = Tile.Verts[v * 3 + 1];
		const float Raw = Tile.Verts[v * 3 + 2];

		float BestD = TNumericLimits<float>::Max();
		float BestElev = 0.0f;
		float BestX = 0.0f, BestY = 0.0f;
		float BestHalfWidthCm = 450.0f;
		for (int32 Idx : Candidates)
		{
			const FRoadStation& St = Hash.Stations[Idx];
			const float Dx = X - St.X;
			const float Dy = Y - St.Y;
			const float D = FMath::Sqrt(Dx * Dx + Dy * Dy);
			if (D < BestD)
			{
				BestD = D;
				BestElev = St.Z;
				BestX = St.X;
				BestY = St.Y;
				BestHalfWidthCm = St.HalfWidthCm;
			}
		}

		// carve_grid_m: is_cut picks the tighter cut_inner/cut_outer taper
		// for a hillside pixel (raw above road) vs the wider inner/outer
		// taper for a fill pixel (raw below road).
		const bool bIsCut = Raw > BestElev;
		const float Inner = bIsCut ? CarveCutInnerCm : CarveInnerCm;
		const float Outer = bIsCut ? CarveCutOuterCm : CarveOuterCm;
		float W = FMath::Clamp((Outer - BestD) / (Outer - Inner), 0.0f, 1.0f);
		W = W * W * (3.0f - 2.0f * W); // smoothstep, matches carve_grid_m
		float NewZ = W * (BestElev - CarveDropCm) + (1.0f - W) * Raw;

		// hard clamp under the literal road footprint - carve_grid_m's
		// under_road guarantee. Radius is THIS VERTEX's own dynamic one (see
		// MaxAdjEdgeCm above), not the pipeline's fixed 7.5 m.
		//
		// 2026-08-27 fix (found via -odverifycarve still showing violations
		// after the per-vertex radius fix above): this must NOT reuse
		// BestElev/BestD from the taper search. BestElev is the NEAREST
		// station only - on a switchback/hairpin stretch (plenty of these in
		// Green Valley) two stations from DIFFERENT passes can sit within a
		// few meters of each other in XY while being many meters apart in Z.
		// A vertex whose nearest station is the higher pass still needs to be
		// pinned below the LOWER pass's elevation if that lower station is
		// also within this vertex's own clamp radius - otherwise the surface
		// legitimately clears "its" nearest station's clamp yet still pokes
		// up over the other station sitting just outside that nearest-search
		// tiebreak. So the clamp target is the MINIMUM elevation among every
		// candidate within DynamicClampRadiusCm, not just the closest one.
		//
		// 2026-08-27 follow-up fix (Taylor's live-drive report: "the road was
		// sitting well above the terrain... using all of the skirt to cover
		// the difference" over long, perfectly ordinary GRADED stretches, not
		// just switchbacks): DynamicClampRadiusCm can be large (up to ~150 m
		// on far/coarse tiles - see that constant's own comment), and taking
		// a flat min-over-XY-radius across EVERY station in range conflates
		// two different things. A genuinely different road pass close by in
		// XY (the switchback case this block exists for) has to win the
		// clamp. But a station further along THIS SAME graded pass, still
		// within the radius, is not a different surface at all - on any real
		// grade (say 5-10%), a station 100-150 m away is legitimately several
		// meters lower, and blindly taking its elevation as this vertex's
		// clamp target drags the WHOLE local terrain down to it, leaving the
		// actual road (interpolated near BestElev, the nearest station) far
		// above the carved surface - exactly the reported "skirt bridging a
		// huge gap" symptom. The two cases are told apart the same way the
		// offline bake's own switchback detector does (road_mesh_common.py's
		// detect_elevation_conflict_pairs/classify_wall_stations): a pair of
		// stations is only a genuine conflict if their elevation gap exceeds
		// what ANY plausible continuous road grade could produce over their
		// OWN XY separation (ConflictMaxGrade/ConflictMinGapCm, ported
		// verbatim from that function's CONFLICT_MAX_GRADE/CONFLICT_MIN_GAP_M
		// so the two carves agree on what counts as "a different road").
		// BestElev (the nearest station) always seeds the min unconditionally
		// - it's this vertex's own best local estimate of the road surface
		// and must never be filtered out - only OTHER, farther candidates
		// need to clear the conflict test before they're allowed to pull the
		// clamp down further.
		// 2026-08-27 (mid-distance road-float fix): Coarse tiles' own ~100 m
		// vertex spacing (FAR_GRID_N=21 over a 2000 m tile) means even the
		// adjacent-edge-derived multiplier below can leave the nearest coarse
		// vertex to a stretch of road corridor just outside the clamp -
		// there simply aren't enough vertices to guarantee one lands close.
		// Near's 1.5x is already proven sufficient (-odverifycarve reports
		// zero violations at that multiplier); Coarse gets a wider 2.5x on
		// top of its already-much-longer MaxAdjEdgeCm, so the clamp actually
		// reaches whichever sparse vertex is standing in for the whole
		// corridor near it, rather than only the taper (Inner/Outer, unchanged
		// and shared with Near) doing the work.
		const float ClampMultiplier = (Fidelity == ETileFidelity::Coarse) ? 2.5f : 1.5f;
		// Floor is the NEAREST station's own literal footprint (half-width +
		// skirt, per-station since ODS2) rather than the fixed 7.5 m - a
		// 4-lane I-70 station carves its full 8+3 m footprint, a narrow
		// oneway ramp doesn't over-carve. Identical to the old constant for
		// classic 4.5 m stations (450 + 300 = HardClampRadiusCm).
		const float FootprintClampCm = BestHalfWidthCm + SkirtExtraCm;
		const float DynamicClampRadiusCm = FMath::Max(FootprintClampCm, MaxAdjEdgeCm[v] * ClampMultiplier);
		const bool bHardClamped = BestD <= DynamicClampRadiusCm;
		if (bHardClamped)
		{
			// RAISE clamp (2026-08-27 fix - this is the actual "road floats
			// above terrain" bug): the taper above only pulls a fill-case
			// vertex (Raw below road) up to BestElev-CarveDropCm gradually,
			// fully engaging only inside CarveInnerCm (50 m) and doing
			// NOTHING at all past CarveOuterCm (150 m). On a sparse Coarse
			// grid a vertex can be the nearest MESH vertex to a road
			// stretch yet still sit outside that taper's full-effect zone,
			// so it never gets raised enough - the road (rendered at its own
			// baked elevation) then visibly floats above it. Mirrors the
			// pre-existing lower/cut hard clamp below (same
			// DynamicClampRadiusCm, same "literal footprint" reasoning) but
			// in the other direction: within the footprint radius, pull the
			// vertex UP to meet the nearest station's own elevation (not the
			// switchback-aware minimum below - a farther, lower conflicting
			// pass has no business raising this vertex, only lowering it).
			NewZ = FMath::Max(NewZ, BestElev - ClampMarginCm);

			float MinElevWithinRadius = BestElev;
			for (int32 Idx : Candidates)
			{
				const FRoadStation& St = Hash.Stations[Idx];
				const float Dx = X - St.X;
				const float Dy = Y - St.Y;
				const float D = FMath::Sqrt(Dx * Dx + Dy * Dy);
				// A candidate's own footprint counts too - a wide (ODS2
				// half-width) station's pavement can reach this vertex even
				// when the vertex's nearest-station-derived radius doesn't.
				if (D > FMath::Max(DynamicClampRadiusCm, St.HalfWidthCm + SkirtExtraCm)
					|| St.Z >= MinElevWithinRadius)
				{
					continue;   // out of range, or can't lower the min further anyway
				}
				const float DBetweenStationsCm = FMath::Sqrt(FMath::Square(St.X - BestX) + FMath::Square(St.Y - BestY));
				const float DzCm = BestElev - St.Z;
				if (DzCm <= ConflictMaxGrade * DBetweenStationsCm + ConflictMinGapCm)
				{
					continue;   // plausibly the same graded pass continuing downhill - not a real conflict
				}
				MinElevWithinRadius = St.Z;
			}
			NewZ = FMath::Min(NewZ, MinElevWithinRadius - ClampMarginCm);
			++OutHardClampedVerts;
		}

		if (bBuildWalls)
		{
			RawZ[v] = Raw;
			HardCut[v] = bHardClamped && ((Raw - NewZ) >= WallThresholdCm);
		}

		if (NewZ != Raw)
		{
			++OutVertsTouched;
			OutMaxDropCm = FMath::Max(OutMaxDropCm, Raw - NewZ);
			OutMaxLiftCm = FMath::Max(OutMaxLiftCm, NewZ - Raw);
		}
		Tile.Verts[v * 3 + 2] = NewZ;
	}

	if (!bBuildWalls)
	{
		return;
	}

	// Phase 2 (default off - see bEnableRuntimeCutWalls): emit a vertical
	// quad between any two mesh-ADJACENT vertices (sharing a triangle edge)
	// that both got hard-clamped down by more than WallThresholdCm - a rough
	// "cut face" ribbon along this tile's own carve hard-clamp boundary.
	// Deliberately NOT a port of road_mesh_common.classify_wall_stations()/
	// build_retaining_walls.py's real algorithm (union-find run clustering +
	// 2-means upper/lower split + PCA-axis ruled surface between two
	// DIFFERENT roads' stations at a genuine elevation conflict) - that
	// operates on the road network's own station graph, which this
	// per-vertex terrain carve has no equivalent of. See
	// AODTerrainManager::bEnableRuntimeCutWalls's comment for the full
	// reasoning on why this simpler heuristic is gated off by default.
	TSet<uint64> SeenEdges;
	const uint32 TCount = Tile.Tris.Num() / 3;
	for (uint32 t = 0; t < TCount; ++t)
	{
		const uint32 TriIdx[3] = { Tile.Tris[t * 3 + 0], Tile.Tris[t * 3 + 1], Tile.Tris[t * 3 + 2] };
		for (int32 e = 0; e < 3; ++e)
		{
			uint32 A = TriIdx[e];
			uint32 B = TriIdx[(e + 1) % 3];
			if (!HardCut[A] || !HardCut[B])
			{
				continue;
			}
			if (A > B) { Swap(A, B); }
			const uint64 EdgeKey = (uint64(A) << 32) | uint64(B);
			bool bAlreadySeen = false;
			SeenEdges.Add(EdgeKey, &bAlreadySeen);
			if (bAlreadySeen)
			{
				continue;
			}

			const int32 Base = Tile.WallVerts.Num() / 3;
			auto AppendVert = [&Tile](float X, float Y, float Z)
			{
				Tile.WallVerts.Add(X);
				Tile.WallVerts.Add(Y);
				Tile.WallVerts.Add(Z);
			};
			AppendVert(Tile.Verts[A * 3 + 0], Tile.Verts[A * 3 + 1], Tile.Verts[A * 3 + 2]); // A, carved
			AppendVert(Tile.Verts[B * 3 + 0], Tile.Verts[B * 3 + 1], Tile.Verts[B * 3 + 2]); // B, carved
			AppendVert(Tile.Verts[A * 3 + 0], Tile.Verts[A * 3 + 1], RawZ[A]);               // A, raw
			AppendVert(Tile.Verts[B * 3 + 0], Tile.Verts[B * 3 + 1], RawZ[B]);               // B, raw
			Tile.WallTris.Add(Base + 0); Tile.WallTris.Add(Base + 2); Tile.WallTris.Add(Base + 1);
			Tile.WallTris.Add(Base + 1); Tile.WallTris.Add(Base + 2); Tile.WallTris.Add(Base + 3);
		}
	}
}

void AODTerrainManager::VerifyCarveGuarantee(const FParsedTerrainTile& Tile, const FIntPoint& Key, float TileSizeCm,
	const FStationSpatialHash& Hash, float ClampMarginCm,
	int32& OutSampled, int32& OutViolations,
	float& OutMaxGapCm, float& OutSumGapCm)
{
	OutSampled = 0;
	OutViolations = 0;
	// 2026-08-27 QA addition (grade-aware clamp fix verification) - how far
	// BELOW each sampled station the carved surface actually sits. The
	// violation check above only guards the surface never poking ABOVE the
	// road; it says nothing about the gap being reasonable in the other
	// direction, which is exactly what the "road floating way above a
	// far-too-low terrain trench" bug looked like. Expected value with the
	// carve working correctly is ~ClampMarginCm (a few tens of cm); a mean/
	// max in the METERS on an ordinary graded (non-switchback) stretch is
	// the bug's signature.
	OutMaxGapCm = 0.0f;
	OutSumGapCm = 0.0f;

	const uint32 TCount = Tile.Tris.Num() / 3;
	if (TCount == 0 || Hash.Stations.Num() == 0)
	{
		return;
	}

	// Exact tile bounds this time (no StationQueryMarginCm padding) - only
	// stations that actually fall INSIDE this tile make sense to spot-check
	// against ITS OWN just-carved surface.
	const float MinX = Key.X * TileSizeCm;
	const float MinY = Key.Y * TileSizeCm;
	const float MaxX = MinX + TileSizeCm;
	const float MaxY = MinY + TileSizeCm;

	TArray<int32> Candidates;
	Hash.QueryBox(MinX, MinY, MaxX, MaxY, Candidates);
	if (Candidates.Num() == 0)
	{
		return;
	}

	// Up to MaxSamples stations, spread evenly across the candidate list
	// (not just the first few, which QueryBox's cell-bucket order could bias
	// toward one corner of the tile) - a spot-check, not exhaustive coverage.
	constexpr int32 MaxSamples = 12;
	const int32 Stride = FMath::Max(1, Candidates.Num() / MaxSamples);

	for (int32 c = 0; c < Candidates.Num() && OutSampled < MaxSamples; c += Stride)
	{
		const FRoadStation& St = Hash.Stations[Candidates[c]];
		if (St.X < MinX || St.X >= MaxX || St.Y < MinY || St.Y >= MaxY)
		{
			// QueryBox's cell-overlap test is coarse - a candidate can belong
			// to a neighboring tile's cell that merely overlaps this box.
			// Only exact in-bounds stations are a fair test of THIS tile's
			// own carved surface.
			continue;
		}

		// Brute-force point-in-triangle search in XY, then barycentric-
		// interpolate Z - deliberately NOT indexing the grid by row/col
		// (would need to assume this tile's flat Verts array is laid out in
		// a specific row-major order the ODT1 format doesn't actually
		// document), so this works regardless of that ordering. Fine for a
		// handful of samples per tile load; not a hot path.
		bool bFound = false;
		float SurfaceZ = 0.0f;
		for (uint32 t = 0; t < TCount && !bFound; ++t)
		{
			const uint32 IA = Tile.Tris[t * 3 + 0];
			const uint32 IB = Tile.Tris[t * 3 + 1];
			const uint32 IC = Tile.Tris[t * 3 + 2];
			const float Ax = Tile.Verts[IA * 3 + 0], Ay = Tile.Verts[IA * 3 + 1];
			const float Bx = Tile.Verts[IB * 3 + 0], By = Tile.Verts[IB * 3 + 1];
			const float Cx = Tile.Verts[IC * 3 + 0], Cy = Tile.Verts[IC * 3 + 1];

			// Standard barycentric-coordinate point-in-triangle test.
			const float Denom = (By - Cy) * (Ax - Cx) + (Cx - Bx) * (Ay - Cy);
			if (FMath::IsNearlyZero(Denom))
			{
				continue; // degenerate triangle
			}
			const float U = ((By - Cy) * (St.X - Cx) + (Cx - Bx) * (St.Y - Cy)) / Denom;
			const float V = ((Cy - Ay) * (St.X - Cx) + (Ax - Cx) * (St.Y - Cy)) / Denom;
			const float W = 1.0f - U - V;
			constexpr float Eps = -0.001f; // tiny slack for edge-adjacent samples
			if (U >= Eps && V >= Eps && W >= Eps)
			{
				SurfaceZ = U * Tile.Verts[IA * 3 + 2] + V * Tile.Verts[IB * 3 + 2] + W * Tile.Verts[IC * 3 + 2];
				bFound = true;
			}
		}
		if (!bFound)
		{
			continue; // station just outside the triangulated area - not a fair sample
		}

		++OutSampled;
		if (SurfaceZ > St.Z - ClampMarginCm)
		{
			++OutViolations;
		}
		const float GapCm = St.Z - SurfaceZ;   // positive = surface below station, as expected
		OutMaxGapCm = FMath::Max(OutMaxGapCm, GapCm);
		OutSumGapCm += GapCm;
	}
}

void AODTerrainManager::ApplyBiomeTint(FParsedTerrainTile& Tile, const FIntPoint& Key, float TileSizeCm,
	const FBiomeSpatialHash& BiomeHash, const FStationSpatialHash& StationHash,
	float TintStrength, const FODGroundBands& Bands, int32& OutVertsTinted, int32& OutDistinctBiomes,
	float& OutMinLuma, float& OutMaxLuma)
{
	OutVertsTinted = 0;
	OutDistinctBiomes = 0;
	OutMinLuma = 1.0f;
	OutMaxLuma = 0.0f;

	const uint32 VCount = Tile.Verts.Num() / 3;
	if (VCount == 0)
	{
		return;
	}

	// Same query-once-per-tile approach ApplyRoadCarve uses above (see its
	// comment) - candidates are already localized to this tile's own
	// neighborhood before the per-vertex loop even starts.
	const float MinX = Key.X * TileSizeCm - StationQueryMarginCm;
	const float MaxX = (Key.X + 1) * TileSizeCm + StationQueryMarginCm;
	const float MinY = Key.Y * TileSizeCm - StationQueryMarginCm;
	const float MaxY = (Key.Y + 1) * TileSizeCm + StationQueryMarginCm;

	TArray<int32> BiomeCandidates;
	BiomeHash.QueryBox(MinX, MinY, MaxX, MaxY, BiomeCandidates);
	TArray<int32> RoadCandidates;
	StationHash.QueryBox(MinX, MinY, MaxX, MaxY, RoadCandidates);

	// NOTE (2026-08-27): a no-data tile no longer early-outs with empty
	// VertColors. The ground material was reworked so the vertex color IS
	// the flat-ground hue (multiplied by a desaturated detail texture) -
	// see unreal/apply_ground_material.py's grass-branch comment. An empty
	// overlay reads as vertex-color white there, i.e. chalk-white ground,
	// so every tile must bake colors now: the loop below already defaults
	// to the Deciduous table entry when no biome candidates exist and
	// skips the verge blend when no road candidates exist, which is
	// exactly the right far-background look. (bEnableBiomeTint=false, the
	// A/B debug toggle, still yields empty overlays and therefore
	// white-ish ground - acceptable for a debug mode.)
	Tile.VertColors.SetNumUninitialized(VCount);
	TSet<uint8> SeenBiomes;
	const float BiomeRadiusSq = BiomeBlendRadiusCm * BiomeBlendRadiusCm;

	for (uint32 v = 0; v < VCount; ++v)
	{
		const float X = Tile.Verts[v * 3 + 0];
		const float Y = Tile.Verts[v * 3 + 1];
		const float Z = Tile.Verts[v * 3 + 2]; // post-carve - ApplyRoadCarve already ran

		// ---- BIOME: inverse-distance blend, mirrors AODForestManager::
		// BlendAt()'s own species-weight blend, just over ground-color
		// entries instead. ----
		FLinearColor GroundTint = BiomeGroundTintTable[(uint8)EODBiome::DeciduousForest].Tint;
		float Northness = 0.0f, Moisture = 0.0f; // V2 drivers, neutral when no data
		if (BiomeCandidates.Num() > 0)
		{
			const FVector2D Here(X, Y);
			float WeightSum = 0.0f;
			FLinearColor Acc(0.0f, 0.0f, 0.0f, 0.0f);
			float NorthAcc = 0.0f, MoistAcc = 0.0f;
			float BestD2 = TNumericLimits<float>::Max();
			const FBiomeSample* Nearest = &BiomeHash.Points[BiomeCandidates[0]];
			for (int32 Idx : BiomeCandidates)
			{
				const FBiomeSample& Pt = BiomeHash.Points[Idx];
				const float D2 = FVector2D::DistSquared(FVector2D(Pt.X, Pt.Y), Here);
				if (D2 < BestD2)
				{
					BestD2 = D2;
					Nearest = &Pt;
				}
				if (D2 > BiomeRadiusSq)
				{
					continue;
				}
				const float W = 1.0f / FMath::Max(1.0f, D2);
				Acc += BiomeGroundTintTable[Pt.Biome].Tint * W;
				NorthAcc += Pt.Northness * W;
				MoistAcc += Pt.Moisture * W;
				WeightSum += W;
			}
			const uint8 NearestBiome = Nearest->Biome;
			if (WeightSum > 0.0f)
			{
				GroundTint = Acc / WeightSum;
				Northness = NorthAcc / WeightSum;
				Moisture = MoistAcc / WeightSum;
			}
			else if (BestD2 <= FarBiomeFallbackCm * FarBiomeFallbackCm)
			{
				// nothing within BiomeBlendRadiusCm, but the nearest sample
				// is still close enough (within FarBiomeFallbackCm) to trust
				// as representative of this location - use it outright, same
				// fallback BlendAt() uses rather than silently reverting to a
				// fixed default biome.
				GroundTint = BiomeGroundTintTable[NearestBiome].Tint;
				Northness = Nearest->Northness;
				Moisture = Nearest->Moisture;
			}
			// else: nearest sample is beyond FarBiomeFallbackCm (this vertex
			// is background terrain far from any road) - keep the
			// already-initialized default GroundTint rather than inheriting
			// an arbitrary, possibly unrepresentative remote road-corridor
			// biome (see FarBiomeFallbackCm's comment above).
			SeenBiomes.Add(NearestBiome);
		}

		// ---- ASPECT + MOISTURE (V2, sec 7.35): within-biome variation on
		// top of the biome base color. South-facing -> sun-baked dry grass/
		// sage; north-facing -> darker (denser, shaded canopy); valley
		// bottoms -> lusher meadow green. ----
		const float South = FMath::Max(0.0f, -Northness);
		GroundTint = FMath::Lerp(GroundTint, DrySlopeTint, South * AspectDryStrength);
		GroundTint *= 1.0f - FMath::Max(0.0f, Northness) * AspectShadeStrength;
		GroundTint = FMath::Lerp(GroundTint, MoistMeadowTint, Moisture * MoistureStrength);

		// ---- ELEVATION: subtle shift, richer/darker low, greyer/rockier
		// high - smoothstep across the bands so there's no visible seam. ----
		const float ElevT = FMath::Clamp((Z - Bands.ElevLowCm) / (Bands.ElevHighCm - Bands.ElevLowCm), 0.0f, 1.0f);
		const float ElevS = ElevT * ElevT * (3.0f - 2.0f * ElevT);
		const FLinearColor LowBiased = FMath::Lerp(GroundTint, ElevLowBias, Bands.LowGreenBias);
		const FLinearColor HighBiased = FMath::Lerp(GroundTint, ElevHighBias, 0.55f);
		FLinearColor Tint = FMath::Lerp(LowBiased, HighBiased, ElevS);

		// ---- ROAD DISTANCE: blend toward a dusty verge tint within
		// VergeOuterCm of the nearest station, same nearest-station search
		// shape as ApplyRoadCarve's own (a second pass, not shared, since
		// ApplyRoadCarve doesn't hand its own BestD back out). ----
		if (RoadCandidates.Num() > 0)
		{
			float BestRoadD = TNumericLimits<float>::Max();
			for (int32 Idx : RoadCandidates)
			{
				const FRoadStation& St = StationHash.Stations[Idx];
				const float Dx = X - St.X, Dy = Y - St.Y;
				BestRoadD = FMath::Min(BestRoadD, FMath::Sqrt(Dx * Dx + Dy * Dy));
			}
			float VergeT = 1.0f - FMath::Clamp((BestRoadD - VergeInnerCm) / (VergeOuterCm - VergeInnerCm), 0.0f, 1.0f);
			VergeT = VergeT * VergeT * (3.0f - 2.0f * VergeT);
			Tint = FMath::Lerp(Tint, RoadVergeTint, VergeT * VergeMaxBlend);
		}

		// ---- NOISE: deterministic position-hash, two octaves (~50 m/~200 m)
		// for large-scale patchiness, blending toward a drier variant. ----
		const float NoiseFine = ValueNoise2D(X, Y, NoiseScaleFineCm, 1);
		const float NoiseCoarse = ValueNoise2D(X, Y, NoiseScaleCoarseCm, 2);
		const float Noise = FMath::Clamp(0.4f * NoiseFine + 0.6f * NoiseCoarse, 0.0f, 1.0f);
		Tint = FMath::Lerp(Tint, Tint * DryVariantMul, Noise * NoiseStrength);

		// ---- STRENGTH: lerp toward neutral white for A/B tuning - see
		// BiomeTintStrength's comment. ----
		const FLinearColor Final = FMath::Lerp(FLinearColor::White, Tint, TintStrength).GetClamped();

		const float Luma = Final.R * 0.2126f + Final.G * 0.7152f + Final.B * 0.0722f;
		OutMinLuma = FMath::Min(OutMinLuma, Luma);
		OutMaxLuma = FMath::Max(OutMaxLuma, Luma);

		// 2026-09-04 FIX: QuantizeRound() is a direct linear->byte mapping
		// (no gamma), pairing with FColor::ReinterpretAsLinear() in BuildTile
		// (just /255) and ColorSpaceMode=NoTransform on the component. The
		// previous ToFColor(true)/FLinearColor(FColor) pairing assumed the
		// UStaticMesh convention - painted vertex colors go through the GPU's
		// hardware sRGB *texture sampler* decode - but UDynamicMeshComponent's
		// color overlay is an ordinary per-vertex attribute, not a sampled
		// texture, so nothing on the GPU side ever un-does the sRGB encode:
		// a dark biome tint like DeciduousForest (0.06,0.16,0.05) got
		// brightened on the way in and never brightened back down, rendering
		// as a washed pale mint instead of the intended dark green - proven
		// by a diagnostic render with MP_BASE_COLOR wired straight to raw
		// VertexColor (bypassing every texture/blend node in M_GroundStylized)
		// still showing pale ground. QuantizeRound/ReinterpretAsLinear/
		// NoTransform is a lossless round trip (8-bit quantization only).
		Tile.VertColors[v] = Final.QuantizeRound();
	}

	OutVertsTinted = (int32)VCount;
	OutDistinctBiomes = SeenBiomes.Num();
}

static FString TerrainTileFilePath(const FString& Prefix, const FIntPoint& Key)
{
	return FPaths::ProjectContentDir() / TEXT("OpenDrive/Masks")
		/ FString::Printf(TEXT("%s_tile_%d_%d.bin"), *Prefix, Key.X, Key.Y);
}

void AODTerrainManager::RequestTileLoad(const FIntPoint& Key, ETileFidelity Fidelity)
{
	TSet<FIntPoint>& Pending = (Fidelity == ETileFidelity::Near) ? PendingNearTiles : PendingCoarseTiles;
	Pending.Add(Key);

	// Near always reads TerrainTilePrefix's own file. Coarse prefers the
	// "terrain_coarse_<area>_..." variant baked alongside every NEAR tile
	// (pipeline/build_terrain_mesh_tiles.py's main()) so a route tile has
	// something cheap to show before it's close enough to warrant the
	// full build; a plain far tile never got that separate file - it only
	// ever had the one file, under the ordinary prefix, which already IS
	// the coarse content - so Coarse falls back to that same path when the
	// "_coarse_" one doesn't exist (2026-08-25: this fallback was missing
	// in the initial cut and silently starved every far/background tile).
	FString PrimaryPath = TerrainTileFilePath(TerrainTilePrefix, Key);
	FString FallbackPath;
	if (Fidelity == ETileFidelity::Coarse)
	{
		checkf(TerrainTilePrefix.StartsWith(TEXT("terrain_")),
			TEXT("TerrainTilePrefix %s must start with \"terrain_\""), *TerrainTilePrefix);
		const FString CoarsePrefix = TEXT("terrain_coarse_") + TerrainTilePrefix.Mid(8);
		FallbackPath = MoveTemp(PrimaryPath);
		PrimaryPath = TerrainTileFilePath(CoarsePrefix, Key);
	}

	// Copied by value into the ThreadPool lambda below rather than touching
	// `this` off the game thread: StationHash is a thread-safe shared
	// pointer to data that never mutates after BeginPlay, and the two wall
	// settings are plain PODs, so a snapshot taken here is exactly as valid
	// as reading the live member would be, without needing WeakThis
	// validity checked before the carve step the way OnTileParsed already
	// checks it before touching any actor/component state.
	const TSharedPtr<FStationSpatialHash, ESPMode::ThreadSafe> HashForCarve = StationHash;
	const bool bBuildRuntimeWalls = bEnableRuntimeCutWalls;
	const float WallThresholdCm = RuntimeCutWallThresholdCm;
	const float CarveTileSizeCm = TileSizeCm;
	const float ClampMarginCm = RoadCarveClampMarginCm;
	// 2026-08-27: was Near-only ("Fidelity == ETileFidelity::Near") - widened
	// to cover Coarse too as part of the mid-distance road-float
	// investigation, since Coarse's own carve guarantee had never actually
	// been spot-checked before (most visible "mid-distance" terrain is
	// Coarse - see AODTerrainManager::CollisionRadius's comment on how small
	// the Near ring really is). Still opt-in via -odverifycarve, so this
	// costs nothing by default.
	const bool bVerifyCarve = bVerifyCarveGuarantee;
	const bool bRuntimeCarve = bEnableRuntimeRoadCarve;

	// Runtime ground tint (2026-08-26) - same snapshot-by-value reasoning as
	// HashForCarve above (BiomeHash is thread-safe/read-only after BeginPlay).
	const TSharedPtr<FBiomeSpatialHash, ESPMode::ThreadSafe> HashForTint = BiomeHash;
	const bool bTintEnabled = bEnableBiomeTint;
	const float TintStrengthValue = BiomeTintStrength;

	// drawn-area outline mask beside the terrain tile (2026-10-21), only read
	// when the unsynced material exists to show it
	const FString OutlinePath = UnsyncedMat && TerrainTilePrefix.StartsWith(TEXT("terrain_"))
		? TerrainTileFilePath(TEXT("outline_") + TerrainTilePrefix.Mid(8), Key) : FString();

	TWeakObjectPtr<AODTerrainManager> WeakThis(this);
	Async(EAsyncExecution::ThreadPool, [WeakThis, Key, Fidelity, PrimaryPath, FallbackPath,
		HashForCarve, bBuildRuntimeWalls, WallThresholdCm, CarveTileSizeCm, ClampMarginCm, bVerifyCarve, bRuntimeCarve,
		HashForTint, bTintEnabled, TintStrengthValue, Bands = GroundBands, OutlinePath]()
	{
		FParsedTerrainTile Parsed;
		// Check existence first rather than just trying-then-falling-back -
		// every plain far tile is a guaranteed miss on the "_coarse_"
		// prefix, and that's most of the tiles in VisualRadius, so
		// avoiding LoadFileToArray's own "failed to read file" warning
		// spam here (LogStreaming) actually matters.
		// (ODTileSource::Resolve does that existence check, and fetches from
		// -ODTileServer when one is set.)
		FString ReadPath;
		EODTileSource Src = ODTileSource::Resolve(PrimaryPath, ReadPath);
		if (Src == EODTileSource::Missing && !FallbackPath.IsEmpty())
		{
			Src = ODTileSource::Resolve(FallbackPath, ReadPath);
		}
		const bool bPending = Src == EODTileSource::Pending;
		const bool bOk = Src == EODTileSource::Ready && ParseTerrainTileFile(ReadPath, Parsed);

		// Outlier-vertex safety net (2026-08-27, phantom-shard investigation)
		// - runs on the RAW parsed tile, before the carve below ever reads
		// Verts, so a corrupt vertex can't poison ApplyRoadCarve's own
		// neighbor-distance math. 20000 cm (200 m) threshold - see
		// RepairOutlierVertices's header comment for why that's generous.
		if (bOk)
		{
			constexpr float OutlierThresholdCm = 20000.0f;
			int32 NumRepaired = 0;
			float MaxOutlierDeltaCm = 0.0f;
			RepairOutlierVertices(Parsed, Key, OutlierThresholdCm, NumRepaired, MaxOutlierDeltaCm);
			if (NumRepaired > 0)
			{
				UE_LOG(LogTemp, Warning,
					TEXT("ODTerrainManager: tile (%d,%d) fidelity=%s repaired %d outlier vertex/vertices (max neighbor delta %.1fm)"),
					Key.X, Key.Y, Fidelity == ETileFidelity::Near ? TEXT("Near") : TEXT("Coarse"),
					NumRepaired, MaxOutlierDeltaCm / 100.0f);
			}
		}

		// Runtime carve (2026-08-26): applied here, off the game thread,
		// BEFORE OnTileParsed/BuildTile ever see this tile's Verts - costs
		// zero frame time on the game thread, same reasoning the parse
		// itself already runs on the thread pool for.
		if (bOk && HashForCarve.IsValid())
		{
			int32 VertsTouched = 0;
			float MaxDropCm = 0.0f;
			float MaxLiftCm = 0.0f;
			int32 HardClampedVerts = 0;
			if (bRuntimeCarve)   // see bEnableRuntimeRoadCarve - off by default
			{
				ApplyRoadCarve(Parsed, Key, CarveTileSizeCm, *HashForCarve, Fidelity,
					bBuildRuntimeWalls, WallThresholdCm, ClampMarginCm, VertsTouched, MaxDropCm, MaxLiftCm, HardClampedVerts);
			}
			if (VertsTouched > 0)
			{
				UE_LOG(LogTemp, Display,
					TEXT("ODTerrainManager: runtime carve tile (%d,%d) fidelity=%s verts=%d touched=%d clamped=%d maxDropCm=%.1f maxLiftCm=%.1f wallVerts=%d"),
					Key.X, Key.Y, Fidelity == ETileFidelity::Near ? TEXT("Near") : TEXT("Coarse"),
					Parsed.Verts.Num() / 3, VertsTouched, HardClampedVerts, MaxDropCm, MaxLiftCm, Parsed.WallVerts.Num() / 3);
			}

			// Temporary QA spot-check (2026-08-26) - see bVerifyCarveGuarantee's
			// comment. Runs on the already-carved Parsed tile, still off the
			// game thread.
			if (bVerifyCarve)
			{
				int32 Sampled = 0, Violations = 0;
				float MaxGapCm = 0.0f, SumGapCm = 0.0f;
				VerifyCarveGuarantee(Parsed, Key, CarveTileSizeCm, *HashForCarve, ClampMarginCm, Sampled, Violations, MaxGapCm, SumGapCm);
				if (Sampled > 0)
				{
					UE_LOG(LogTemp, Display,
						TEXT("ODTerrainManager: carve-guarantee check tile (%d,%d) sampled=%d violations=%d ")
						TEXT("gapCm(mean=%.1f max=%.1f)"),
						Key.X, Key.Y, Sampled, Violations, SumGapCm / Sampled, MaxGapCm);
				}
			}
		}

		// Runtime ground tint (2026-08-26): also applied here, off the game
		// thread, right after the carve (so elevation reads the CARVED Z,
		// not the raw pre-carve one) and before OnTileParsed/BuildTile ever
		// see this tile. HashForTint may be null (no biome sidecar loaded) -
		// ApplyBiomeTint still runs against an empty hash in that case
		// (elevation/road/noise signals only, see its own fallback).
		if (bOk && bTintEnabled)
		{
			static const FBiomeSpatialHash EmptyBiomeHash;
			static const FStationSpatialHash EmptyStationHash;
			int32 VertsTinted = 0, DistinctBiomes = 0;
			float MinLuma = 0.0f, MaxLuma = 0.0f;
			ApplyBiomeTint(Parsed, Key, CarveTileSizeCm,
				HashForTint.IsValid() ? *HashForTint : EmptyBiomeHash,
				HashForCarve.IsValid() ? *HashForCarve : EmptyStationHash,
				TintStrengthValue, Bands, VertsTinted, DistinctBiomes, MinLuma, MaxLuma);
			if (VertsTinted > 0)
			{
				UE_LOG(LogTemp, Display,
					TEXT("ODTerrainManager: ground tint tile (%d,%d) fidelity=%s verts=%d distinctBiomes=%d lumaMin=%.3f lumaMax=%.3f"),
					Key.X, Key.Y, Fidelity == ETileFidelity::Near ? TEXT("Near") : TEXT("Coarse"),
					VertsTinted, DistinctBiomes, MinLuma, MaxLuma);
			}
		}

		if (bOk)
		{
			Parsed.Mesh = MakeShared<FDynamicMesh3>();
			FillTileMesh(*Parsed.Mesh, Parsed);
			if (!OutlinePath.IsEmpty())
			{
				TagOutsideOutline(*Parsed.Mesh, FVector2D(Parsed.Verts[0], Parsed.Verts[1]), OutlinePath);
			}
		}

		AsyncTask(ENamedThreads::GameThread,
			[WeakThis, Key, Fidelity, bOk, bPending, Parsed = MoveTemp(Parsed)]() mutable
			{
				if (!WeakThis.IsValid() || WeakThis->GetWorld() == nullptr)
				{
					return;
				}
				if (bPending)
				{
					WeakThis->WasPendingTiles.Add(Key);   // dissolves in when it lands (2026-10-21)
					ODTileSource::RetryLater(WeakThis.Get(),
						Fidelity == ETileFidelity::Near ? WeakThis->PendingNearTiles : WeakThis->PendingCoarseTiles, Key);
					return;
				}
				WeakThis->OnTileParsed(Key, Fidelity, bOk, MoveTemp(Parsed));
			});
	});
}

void AODTerrainManager::OnTileParsed(const FIntPoint& Key, ETileFidelity Fidelity, bool bOk, FParsedTerrainTile&& Parsed)
{
	TSet<FIntPoint>& Pending = (Fidelity == ETileFidelity::Near) ? PendingNearTiles : PendingCoarseTiles;
	Pending.Remove(Key);
	if (!bOk)
	{
		// For Coarse, also the expected outcome for any tile beyond the
		// pipeline's render-distance cutoff (build_terrain_mesh_tiles.py
		// never writes a file for those) - not a bug, same "no hole"
		// semantics AODRoadManager relies on for its own MissingTiles. For
		// Near, the normal, expected state for every plain far tile (they
		// never had a near variant) - upgrade attempts for those keys
		// simply stop here forever, which is correct.
		TSet<FIntPoint>& Missing = (Fidelity == ETileFidelity::Near) ? MissingNearTiles : MissingCoarseTiles;
		Missing.Add(Key);
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
			TEXT("ODTerrainManager: tile (%d,%d) generation id %s does not match "
			     "%s's first-loaded generation id %s - tiles from different pipeline "
			     "runs may be mixed (AUDIT-RELIABILITY.md SS3.1/SS5.1)"),
			Key.X, Key.Y, *Parsed.GenerationId, *TerrainTilePrefix, *FirstGenerationId);
	}

	// Actual mesh/collision build is deferred and budgeted (see
	// ProcessPendingTileBuilds) rather than done here synchronously -
	// 2026-08-25 Arc 7 rescope, same reasoning as
	// AODRoadManager::OnTileParsed's PendingChunkBuilds queue.
	FPendingTerrainBuild& Build = PendingTileBuilds.AddDefaulted_GetRef();
	Build.Key = Key;
	Build.Parsed = MoveTemp(Parsed);
	Build.TargetFidelity = Fidelity;
}

void AODTerrainManager::ProcessPendingTileBuilds()
{
	// Millisecond budget, not a tile count (2026-08-27, fast-drive hitch
	// investigation) - see TileBuildMsBudget's comment for why a bare count
	// doesn't actually bound frame time. Always finishes at least one tile
	// per tick (the Num()>0 && ElapsedMs<Budget condition below only stops
	// AFTER a tile completes, never mid-tile) so the queue can't stall even
	// if a single tile alone busts the budget.
	const double StartTime = FPlatformTime::Seconds();
	double ElapsedMs = 0.0;
	int32 NumBuilt = 0;
	while (PendingTileBuilds.Num() > 0 && (NumBuilt == 0 || ElapsedMs < TileBuildMsBudget))
	{
		FPendingTerrainBuild Build = MoveTemp(PendingTileBuilds[0]);
		PendingTileBuilds.RemoveAt(0);

		// A downgrade/upgrade target may have gone stale (tile fully
		// released, or the opposite fidelity already won a race) - only
		// act on it if still relevant.
		FTerrainTile* Existing = Tiles.Find(Build.Key);
		if (Existing && Existing->Fidelity == Build.TargetFidelity)
		{
			ElapsedMs = (FPlatformTime::Seconds() - StartTime) * 1000.0;
			continue;
		}

		if (Existing && Existing->Comp)
		{
			Existing->Comp->DestroyComponent();
			if (Existing->WallComp)
			{
				Existing->WallComp->DestroyComponent();
			}
			Tiles.Remove(Build.Key);
		}

		FTerrainTile NewTile;
		BuildTile(NewTile, Build.Parsed, Build.TargetFidelity);
		if (WasPendingTiles.Remove(Build.Key) > 0)
		{
			SpawnSyncIn(NewTile.Comp);
		}
		Tiles.Add(Build.Key, MoveTemp(NewTile));
		++NumBuilt;

		ElapsedMs = (FPlatformTime::Seconds() - StartTime) * 1000.0;
	}
}

// Instrumentation (2026-08-27, fast-drive hitch investigation): per-stage
// game-thread timing for one terrain tile build - see BuildTile below for
// where each stage boundary is. Logged unconditionally (one Display line per
// built tile, not throttled - these are per-build events, not per-tick) plus
// a Warning whenever a single build's total exceeds WarnThresholdMs, so a
// live drive's log can be grepped for the actual worst offenders instead of
// guessing which stage dominates.
static constexpr double TerrainBuildWarnThresholdMs = 8.0;

// The tile's render mesh, local to its first vertex (BuildTile's Origin).
// Plain data - the parse thread calls this (see FParsedTerrainTile::Mesh).
static void FillTileMesh(FDynamicMesh3& Mesh, const FParsedTerrainTile& Parsed)
{
	const uint32 VCount = Parsed.Verts.Num() / 3;
	const uint32 TCount = Parsed.Tris.Num() / 3;
	const FVector3d Origin = VCount ? FVector3d(Parsed.Verts[0], Parsed.Verts[1], 0.0) : FVector3d::ZeroVector;
	const bool bHasTint = (uint32)Parsed.VertColors.Num() == VCount;
	Mesh.EnableAttributes();
	FDynamicMeshUVOverlay* UV = Mesh.Attributes()->PrimaryUV();
	FDynamicMeshColorOverlay* Color = nullptr;
	if (bHasTint)
	{
		Mesh.Attributes()->EnablePrimaryColors();
		Color = Mesh.Attributes()->PrimaryColors();
	}

	TArray<int32> VertIds; VertIds.SetNumUninitialized(VCount);
	TArray<int32> UvIds; UvIds.SetNumUninitialized(VCount);
	TArray<int32> ColorIds; if (Color) { ColorIds.SetNumUninitialized(VCount); }

	for (uint32 v = 0; v < VCount; ++v)
	{
		const float X = Parsed.Verts[v * 3 + 0];
		const float Y = Parsed.Verts[v * 3 + 1];
		const float Z = Parsed.Verts[v * 3 + 2];
		VertIds[v] = Mesh.AppendVertex(FVector3d(X - Origin.X, Y - Origin.Y, Z));
	}
	for (uint32 v = 0; v < VCount; ++v)
	{
		UvIds[v] = UV->AppendElement(
			FVector2f(Parsed.Uvs[v * 2 + 0], Parsed.Uvs[v * 2 + 1]));
	}
	if (Color)
	{
		for (uint32 v = 0; v < VCount; ++v)
		{
			// ReinterpretAsLinear() is a direct /255 unpack (no sRGB
			// decode) - pairs with ApplyBiomeTint's QuantizeRound() and
			// ColorSpaceMode=NoTransform below (2026-09-04 fix, see
			// ApplyBiomeTint's own comment on the encode side for why).
			const FLinearColor Lin = Parsed.VertColors[v].ReinterpretAsLinear();
			ColorIds[v] = Color->AppendElement(FVector4f(Lin.R, Lin.G, Lin.B, Lin.A));
		}
	}
	for (uint32 t = 0; t < TCount; ++t)
	{
		const uint32 I0 = Parsed.Tris[t * 3 + 0];
		const uint32 I1 = Parsed.Tris[t * 3 + 1];
		const uint32 I2 = Parsed.Tris[t * 3 + 2];
		const int32 Tid = Mesh.AppendTriangle(VertIds[I0], VertIds[I1], VertIds[I2]);
		if (Tid >= 0)
		{
			UV->SetTriangle(Tid, FIndex3i(UvIds[I0], UvIds[I1], UvIds[I2]));
			if (Color)
			{
				Color->SetTriangle(Tid, FIndex3i(ColorIds[I0], ColorIds[I1], ColorIds[I2]));
			}
		}
	}

	// M_GroundStylized's slope-based grass/rock blend reads
	// MaterialExpressionVertexNormalWS - without this, the normal
	// overlay is left empty and the material shades every tile as flat
	// black/near-black regardless of actual slope (the root cause of
	// the "no grass, no floor" bug: exposure/material-load were both
	// fine, the mesh just never had real normals).
	FMeshNormals::InitializeOverlayToPerVertexNormals(Mesh.Attributes()->PrimaryNormals(), false);
}

// Outline mask (2026-10-21, area picker; docs/area-picker-contract.md
// "Outline mask"): material ID 1 (UnsyncedMat) on every triangle whose
// centroid samples below 0 in the tile's outline_* signed-distance grid. No
// file = the tile is wholly inside (or a hand area) and nothing changes.
// Parse thread - plain data only. Mesh verts are local to Origin.
static void TagOutsideOutline(FDynamicMesh3& Mesh, const FVector2D& Origin, const FString& MaskPath)
{
	TArray<uint8> Bytes;
	if (!FPlatformFileManager::Get().GetPlatformFile().FileExists(*MaskPath) || !FFileHelper::LoadFileToArray(Bytes, *MaskPath)
		|| Bytes.Num() < 8 || FMemory::Memcmp(Bytes.GetData(), "ODO1", 4) != 0)
	{
		return;
	}
	int64 At = 4;
	auto Read = [&](void* Out, int64 Size)
	{
		if (At + Size > Bytes.Num())
		{
			return false;
		}
		FMemory::Memcpy(Out, Bytes.GetData() + At, Size);
		At += Size;
		return true;
	};
	uint32 L = 0, N = 0;
	float X0 = 0.0f, Y0 = 0.0f, Size = 0.0f;
	if (!Read(&L, 4) || (At += L) > Bytes.Num() || !Read(&N, 4) || !Read(&X0, 4) || !Read(&Y0, 4) || !Read(&Size, 4)
		|| N < 2 || Size <= 0.0f || At + (int64)N * N * 4 > Bytes.Num())
	{
		UE_LOG(LogTemp, Warning, TEXT("ODTerrainManager: bad outline mask %s"), *MaskPath);
		return;
	}
	const uint8* D = Bytes.GetData() + At;
	auto Cell = [&](int32 R, int32 C)
	{
		float V;
		FMemory::Memcpy(&V, D + ((int64)R * N + C) * 4, 4);
		return (double)V;
	};
	auto Sample = [&](double X, double Y)
	{
		const double C = FMath::Clamp((X - X0) / Size * (N - 1), 0.0, N - 1.001);
		const double R = FMath::Clamp((Y - Y0) / Size * (N - 1), 0.0, N - 1.001);
		const int32 C0 = (int32)C, R0 = (int32)R;
		const double Fc = C - C0, Fr = R - R0;
		return FMath::Lerp(FMath::Lerp(Cell(R0, C0), Cell(R0, C0 + 1), Fc),
			FMath::Lerp(Cell(R0 + 1, C0), Cell(R0 + 1, C0 + 1), Fc), Fr);
	};
	Mesh.Attributes()->EnableMaterialID();
	FDynamicMeshMaterialAttribute* Mat = Mesh.Attributes()->GetMaterialID();
	for (const int32 Tid : Mesh.TriangleIndicesItr())
	{
		const FVector3d C = Mesh.GetTriCentroid(Tid);
		Mat->SetValue(Tid, Sample(C.X + Origin.X, C.Y + Origin.Y) < 0.0 ? 1 : 0);
	}
}

void AODTerrainManager::BuildTile(FTerrainTile& Tile, const FParsedTerrainTile& Parsed, ETileFidelity Fidelity)
{
	const double StageStart = FPlatformTime::Seconds();
	double TCreate = 0.0, TMeshBuild = 0.0, TNormals = 0.0, TCollision = 0.0, TMaterial = 0.0, TWall = 0.0;
	auto Lap = [](double& Since) -> double
	{
		const double Now = FPlatformTime::Seconds();
		const double DeltaMs = (Now - Since) * 1000.0;
		Since = Now;
		return DeltaMs;
	};
	double Clock = StageStart;

	const uint32 VCount = Parsed.Verts.Num() / 3;
	const uint32 TCount = Parsed.Tris.Num() / 3;

	auto* Comp = NewObject<UDynamicMeshComponent>(this);
	Comp->SetMeshDrawPath(EDynamicMeshDrawPath::StaticDraw);   // built once: cached draw commands, not re-gathered every frame per view/shadow view
	Comp->SetupAttachment(RootComponent);
	const FVector3d Origin = VCount ? FVector3d(Parsed.Verts[0], Parsed.Verts[1], 0.0) : FVector3d::ZeroVector;
	Comp->SetRelativeLocation(Origin);   // local origin: see AODRoadManager::CreateChunkMeshComponent (large-world precision)
	// Async collision cook (2026-08-27, fast-drive hitch fix): the physics
	// mesh cook triggered by EnableComplexAsSimpleCollision/RegisterComponent
	// below is exactly the "collision cook" stage TCollision times - moving
	// it off the game thread (UDynamicMeshComponent::bUseAsyncCooking, see
	// BaseDynamicMeshComponent.h) is the documented, engine-supported way to
	// take that cost off the synchronous build path entirely: RebuildPhysicsData
	// dispatches BodySetup->CreatePhysicsMeshesAsync on the thread pool and only
	// calls the cheap RecreatePhysicsState() on the game thread once the cook
	// completes (FinishPhysicsAsyncCook), spread across whatever frame that
	// lands on rather than bursting every Near tile's cook into its own build
	// frame. This does NOT add same-frame collision REGISTRATIONS beyond what
	// already happens - RegisterComponent() still runs once, synchronously,
	// same as before; only the expensive triangle-mesh cook itself moves off
	// this frame, so it doesn't reintroduce the bulk-registration Chaos bug
	// TileBuildMsBudget's own comment describes.
	Comp->bUseAsyncCooking = true;
	Comp->RegisterComponent();
	TCreate = Lap(Clock);

	// Runtime ground tint (2026-08-26): only add the color overlay when
	// ApplyBiomeTint actually populated one - a tile with an empty
	// VertColors (tint disabled, or nothing nearby to tint against) gets no
	// overlay at all, which the material's vertex-color multiply reads as
	// neutral white (see FParsedTerrainTile::VertColors's comment).
	const bool bHasTint = (uint32)Parsed.VertColors.Num() == VCount;

	if (Parsed.Mesh.IsValid())
	{
		Comp->SetMesh(MoveTemp(*Parsed.Mesh));
	}
	else
	{
		Comp->EditMesh([&](FDynamicMesh3& Mesh) { FillTileMesh(Mesh, Parsed); });
	}
	TMeshBuild = Lap(Clock) - TNormals;

	if (bHasTint)
	{
		// 2026-09-04 fix: NoTransform - see the comment above the
		// color-overlay population loop. LinearToSRGB re-encoded the overlay
		// on the way to the GPU with nothing on the other end to decode it
		// back (a dynamic mesh color overlay is a plain vertex attribute,
		// not a sampled sRGB texture), so every baked tint rendered
		// noticeably brighter/paler than ApplyBiomeTint actually computed.
		Comp->SetVertexColorSpaceTransformMode(EDynamicMeshVertexColorTransformMode::NoTransform);
	}

	// Fidelity, not the file's own baked bHasCollision byte, decides
	// collision here: a near tile's coarse fallback carries bHasCollision
	// false same as a plain far tile, but we still want the Near-fidelity
	// *build* of that same tile key to get collision even though the file
	// read for the Coarse request never will.
	if (Fidelity == ETileFidelity::Near && Parsed.bHasCollision)
	{
		Comp->EnableComplexAsSimpleCollision();
		Comp->SetCollisionProfileName(TEXT("BlockAll"));
	}
	else
	{
		// far/background/not-yet-approached tile - paying for collision
		// setup here is pure waste until (if ever) the player gets close.
		Comp->SetCollisionProfileName(TEXT("NoCollision"));
	}
	TCollision = Lap(Clock);

	if (TerrainMID)
	{
		Comp->SetMaterial(0, TerrainMID);
	}
	if (UnsyncedMat)
	{
		Comp->SetMaterial(1, UnsyncedMat);   // TagOutsideOutline's triangles, if any
	}
	TMaterial = Lap(Clock);

	Tile.Comp = Comp;
	Tile.Fidelity = Fidelity;

	// Phase 2 runtime cut-wall ribbon (bEnableRuntimeCutWalls, default off) -
	// built here rather than as its own PendingTerrainBuild entry so it
	// naturally rides the same per-tick TileBuildMsBudget as the terrain
	// mesh it came from, satisfying the "no new same-frame bulk collision
	// creation" constraint without a second budgeted queue.
	if (Parsed.WallVerts.Num() > 0)
	{
		const uint32 WVCount = Parsed.WallVerts.Num() / 3;
		const uint32 WTCount = Parsed.WallTris.Num() / 3;

		auto* WallComp = NewObject<UDynamicMeshComponent>(this);
		WallComp->SetMeshDrawPath(EDynamicMeshDrawPath::StaticDraw);   // built once: cached draw commands, not re-gathered every frame per view/shadow view
		WallComp->SetupAttachment(RootComponent);
		const FVector3d WallOrigin(Parsed.WallVerts[0], Parsed.WallVerts[1], 0.0);
		WallComp->SetRelativeLocation(WallOrigin);   // local origin: see AODRoadManager::CreateChunkMeshComponent (large-world precision)
		WallComp->bUseAsyncCooking = true; // see Comp's own bUseAsyncCooking comment above
		WallComp->RegisterComponent();

		WallComp->EditMesh([&](FDynamicMesh3& Mesh)
		{
			Mesh.EnableAttributes();
			TArray<int32> WallVertIds; WallVertIds.SetNumUninitialized(WVCount);
			for (uint32 v = 0; v < WVCount; ++v)
			{
				WallVertIds[v] = Mesh.AppendVertex(FVector3d(
					Parsed.WallVerts[v * 3 + 0] - WallOrigin.X, Parsed.WallVerts[v * 3 + 1] - WallOrigin.Y, Parsed.WallVerts[v * 3 + 2]));
			}
			for (uint32 t = 0; t < WTCount; ++t)
			{
				Mesh.AppendTriangle(WallVertIds[Parsed.WallTris[t * 3 + 0]],
					WallVertIds[Parsed.WallTris[t * 3 + 1]], WallVertIds[Parsed.WallTris[t * 3 + 2]]);
			}
			FMeshNormals::InitializeOverlayToPerVertexNormals(Mesh.Attributes()->PrimaryNormals(), false);
		});

		// Always-collide, same as ODWallManager's own baked walls - a
		// runtime cut-wall only ever exists directly on top of a hard-clamp
		// the carve just applied under the road, so it's on the drivable
		// corridor by construction.
		WallComp->EnableComplexAsSimpleCollision();
		WallComp->SetCollisionProfileName(TEXT("BlockAll"));
		if (TerrainMID)
		{
			WallComp->SetMaterial(0, TerrainMID);
		}
		Tile.WallComp = WallComp;
		TWall = Lap(Clock);
	}

	const double TotalMs = (FPlatformTime::Seconds() - StageStart) * 1000.0;
	// One line per built tile, unthrottled (per-build event, not per-tick) -
	// stage breakdown for the fast-drive hitch investigation (2026-08-27).
	// TCollision is now just the flag/profile-name assignment (bUseAsyncCooking
	// moved the actual physics cook off this synchronous path - see Comp's own
	// bUseAsyncCooking comment above), so a big TCollision here would mean the
	// async path silently isn't engaging, not that the cook itself is slow.
	UE_LOG(LogTemp, Verbose,
		TEXT("ODTerrainManager: BuildTile (%s) verts=%d tris=%d totalMs=%.2f ")
		TEXT("[create=%.2f meshBuild=%.2f normals=%.2f collisionSetup=%.2f material=%.2f wall=%.2f]"),
		Fidelity == ETileFidelity::Near ? TEXT("Near") : TEXT("Coarse"), VCount, TCount, TotalMs,
		TCreate, TMeshBuild, TNormals, TCollision, TMaterial, TWall);
	if (TotalMs > TerrainBuildWarnThresholdMs)
	{
		UE_LOG(LogTemp, Warning,
			TEXT("ODTerrainManager: BuildTile (%s) exceeded %.1fms threshold - totalMs=%.2f ")
			TEXT("[create=%.2f meshBuild=%.2f normals=%.2f collisionSetup=%.2f material=%.2f wall=%.2f]"),
			Fidelity == ETileFidelity::Near ? TEXT("Near") : TEXT("Coarse"), TerrainBuildWarnThresholdMs, TotalMs,
			TCreate, TMeshBuild, TNormals, TCollision, TMaterial, TWall);
	}
}

void AODTerrainManager::ReleaseTile(const FIntPoint& Key)
{
	if (FTerrainTile* Tile = Tiles.Find(Key))
	{
		if (Tile->Comp)
		{
			Tile->Comp->DestroyComponent();
		}
		if (Tile->WallComp)
		{
			Tile->WallComp->DestroyComponent();
		}
		Tiles.Remove(Key);
	}
}

void AODTerrainManager::Tick(float DeltaSeconds)
{
	Super::Tick(DeltaSeconds);
	static FODTickMeter Meter(TEXT("ODTerrainManager"));   // see ODTickMeter.h
	FODTickMeter::FScope MeterScope(Meter);

	ProcessPendingTileBuilds();

	if (HorizonComp && UnsyncedMat && GetWorld()->GetRealTimeSeconds() >= NextHorizonCheck)
	{
		NextHorizonCheck = GetWorld()->GetRealTimeSeconds() + 1.0;
		const uint32 V = ODBakeLink::StatusVersion();
		if (V != HorizonStatusVersion && ODBakeLink::ActiveOutline().IsValid())
		{
			HorizonStatusVersion = V;
			RetagHorizon();
		}
	}

	FVector Loc;
	if (!Vista.StreamCenter(this, Loc))
	{
		return;
	}
	UpdateRoadDistTex(Loc);
	const FIntPoint Center(FMath::FloorToInt(Loc.X / TileSizeCm),
	                       FMath::FloorToInt(Loc.Y / TileSizeCm));

	// unlike road chunks, a terrain tile's geometry never extends beyond
	// its own tile bounds (build_terrain_mesh_tiles.py builds a plain grid
	// clipped exactly to [tx*TileSizeCm, (tx+1)*TileSizeCm)) - no
	// chunk-span padding needed on the load reach.
	const int32 VisualLoadReach = FMath::CeilToInt(VisualRadius / TileSizeCm);
	const int32 VisualDespawnReach = FMath::CeilToInt((VisualRadius + DespawnMarginCm) / TileSizeCm);
	const int32 CollisionUpgradeReach = FMath::CeilToInt(CollisionRadius / TileSizeCm);
	const int32 CollisionDowngradeReach = FMath::CeilToInt((CollisionRadius + DespawnMarginCm) / TileSizeCm);

	int32 Dispatched = 0;

	// Pass 1: coarse "always something there" load, out to VisualRadius -
	// this is the one that used to be the whole story (as SpawnRadius).
	for (int32 Ring = 0; Ring <= VisualLoadReach && Dispatched < MaxNewLoadsPerTick; ++Ring)
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
				// vista: coarse out to the panorama reach (2026-10-05 trial)
				if (Tiles.Contains(Key) || PendingCoarseTiles.Contains(Key) || MissingCoarseTiles.Contains(Key)
					|| !Vista.WantsTile(Key, TileSizeCm, 0.0, FMath::Max(Vista.FarCm, Vista.PanoFarCm)))
				{
					continue;
				}
				RequestTileLoad(Key, ETileFidelity::Coarse);
				++Dispatched;
			}
		}
	}

	// Pass 2: upgrade coarse -> near for whatever's within the much
	// smaller CollisionRadius (Arc 7 rescope, 2026-08-25) - only tiles
	// that are actually near-corridor route tiles have a near variant to
	// upgrade to; everything else just no-ops into MissingNearTiles once.
	for (int32 Ring = 0; Ring <= CollisionUpgradeReach && Dispatched < MaxNewLoadsPerTick; ++Ring)
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
				const FTerrainTile* Existing = Tiles.Find(Key);
				// vista: Near only inside the full-detail wedge, panorama stays coarse
				if (!Existing || Existing->Fidelity == ETileFidelity::Near
					|| PendingNearTiles.Contains(Key) || MissingNearTiles.Contains(Key)
					|| !Vista.WantsTile(Key, TileSizeCm))
				{
					continue;
				}
				RequestTileLoad(Key, ETileFidelity::Near);
				++Dispatched;
			}
		}
	}

	// Downgrade near -> coarse for anything that's fallen back outside
	// CollisionRadius (+hysteresis) - re-requests the coarse variant
	// through the normal budgeted path rather than swapping instantly.
	for (const auto& Pair : Tiles)
	{
		if (Pair.Value.Fidelity != ETileFidelity::Near)
		{
			continue;
		}
		const int32 Dist = FMath::Max(FMath::Abs(Pair.Key.X - Center.X), FMath::Abs(Pair.Key.Y - Center.Y));
		if (Dist > CollisionDowngradeReach && !PendingCoarseTiles.Contains(Pair.Key))
		{
			RequestTileLoad(Pair.Key, ETileFidelity::Coarse);
		}
	}

	TArray<FIntPoint> Gone;
	for (const auto& Pair : Tiles)
	{
		if (FMath::Max(FMath::Abs(Pair.Key.X - Center.X),
		               FMath::Abs(Pair.Key.Y - Center.Y)) > VisualDespawnReach)
		{
			Gone.Add(Pair.Key);
		}
	}
	for (const FIntPoint& Key : Gone)
	{
		ReleaseTile(Key);
	}

	if (const double Now = GetWorld()->GetTimeSeconds(); Now - LastDiagTime > 5.0)
	{
		LastDiagTime = Now;
		int32 NumNear = 0, NumCoarse = 0;
		for (const auto& Pair : Tiles)
		{
			(Pair.Value.Fidelity == ETileFidelity::Near ? NumNear : NumCoarse)++;
		}
		UE_LOG(LogTemp, Display,
			TEXT("ODTerrainManager: tiles=%d (near=%d coarse=%d) pending=%d buildQueue=%d missing=%d"),
			Tiles.Num(), NumNear, NumCoarse,
			PendingCoarseTiles.Num() + PendingNearTiles.Num(), PendingTileBuilds.Num(),
			MissingCoarseTiles.Num() + MissingNearTiles.Num());
	}
}

TArray<FColor> AODTerrainManager::BuildRoadDistTexels(const FStationSpatialHash& Hash, const FVector2D& Center)
{
	constexpr int32 N = RoadDistTexels;
	const double X0 = Center.X - 0.5 * N * RoadDistTexelCm;   // texel (0,0)'s corner
	const double Y0 = Center.Y - 0.5 * N * RoadDistTexelCm;
	const double Span = N * RoadDistTexelCm;
	TArray<float> Dist;
	Dist.Init(RoadDistRangeCm, N * N);

	TArray<int32> Cand;
	const float Pad = RoadDistRangeCm + 1500.0f;   // + widest half-width + one station step
	Hash.QueryBox(X0 - Pad, Y0 - Pad, X0 + Span + Pad, Y0 + Span + Pad, Cand);
	const TArray<FRoadStation>& S = Hash.Stations;
	for (const int32 i : Cand)
	{
		const FRoadStation& A = S[i];
		const FRoadStation& B = S[NextRunStation(S, i)];
		const float Reach = FMath::Max(A.HalfWidthCm, B.HalfWidthCm) + RoadDistRangeCm;
		const int32 Ix0 = FMath::Max(0, FMath::FloorToInt((FMath::Min(A.X, B.X) - Reach - X0) / RoadDistTexelCm));
		const int32 Ix1 = FMath::Min(N - 1, FMath::FloorToInt((FMath::Max(A.X, B.X) + Reach - X0) / RoadDistTexelCm));
		const int32 Iy0 = FMath::Max(0, FMath::FloorToInt((FMath::Min(A.Y, B.Y) - Reach - Y0) / RoadDistTexelCm));
		const int32 Iy1 = FMath::Min(N - 1, FMath::FloorToInt((FMath::Max(A.Y, B.Y) + Reach - Y0) / RoadDistTexelCm));
		for (int32 iy = Iy0; iy <= Iy1; ++iy)
		{
			const double Py = Y0 + (iy + 0.5) * RoadDistTexelCm;
			for (int32 ix = Ix0; ix <= Ix1; ++ix)
			{
				float& D = Dist[iy * N + ix];
				D = FMath::Min(D, SegmentEdgeDistCm(A, B, X0 + (ix + 0.5) * RoadDistTexelCm, Py));
			}
		}
	}

	// linear -> sRGB once per byte step instead of a pow per texel
	uint8 Lut[1025];
	for (int32 k = 0; k <= 1024; ++k)
	{
		Lut[k] = FLinearColor(k / 1024.0f, 0.0f, 0.0f).ToFColor(true).R;
	}
	TArray<FColor> Out;
	Out.SetNumUninitialized(N * N);
	for (int32 k = 0; k < N * N; ++k)
	{
		const int32 iy = k / N, ix = k % N;
		// border ring reads "no road" so the clamp-addressed sampler does too past the coverage
		const bool bBorder = ix == 0 || iy == 0 || ix == N - 1 || iy == N - 1;
		const uint8 V = bBorder ? 255 : Lut[FMath::RoundToInt(Dist[k] / RoadDistRangeCm * 1024.0f)];
		Out[k] = FColor(V, V, V, 255);
	}
	return Out;
}

void AODTerrainManager::UpdateRoadDistTex(const FVector& Loc)
{
	if (!TerrainMID || !StationHash.IsValid())
	{
		return;
	}
	if (RoadDistJob.IsValid())
	{
		if (!RoadDistJob.IsReady())
		{
			return;
		}
		constexpr int32 N = RoadDistTexels;
		const double PublishT0 = FPlatformTime::Seconds();
		TArray<FColor> Texels = RoadDistJob.Consume();
		if (!RoadDistTex)
		{
			RoadDistTex = UTexture2D::CreateTransient(N, N, PF_B8G8R8A8, NAME_None,
				TConstArrayView64<uint8>((const uint8*)Texels.GetData(), (int64)N * N * sizeof(FColor)));
			RoadDistTex->SRGB = true;
			RoadDistTex->AddressX = TA_Clamp;
			RoadDistTex->AddressY = TA_Clamp;
			RoadDistTex->UpdateResource();   // render-thread init, queued ahead of the origin change below
			TerrainMID->SetTextureParameterValue(TEXT("RoadDistTex"), RoadDistTex);
		}
		else
		{
			// 2026-10-09 (Taylor: "jitter and pull" every ~2/3 s): a fresh 4 MB
			// texture + MID rebind per recenter cost a ~46 ms frame every 32 m
			// (-odtrace ODJolt lines), and Chaos's 1/30 s step clamp then dropped
			// ~30% of that frame's motion. Now one texture, refilled on the render
			// thread - the copy is queued ahead of the origin change below, so
			// texels and origin still land in the same frame.
			TArray<FColor>* Data = new TArray<FColor>(MoveTemp(Texels));
			FUpdateTextureRegion2D* Region = new FUpdateTextureRegion2D(0, 0, 0, 0, N, N);
			RoadDistTex->UpdateTextureRegions(0, 1, Region, N * sizeof(FColor), sizeof(FColor), (uint8*)Data->GetData(),
				[Data, Region](uint8*, const FUpdateTextureRegion2D*) { delete Data; delete Region; });
		}
		RoadDistCenter = RoadDistJobCenter;
		// tile components sit at actor-relative positions (menu vista copies are offset).
		// Double param: a float origin lost world-minus-origin to LWC precision.
		const FVector Off = GetActorLocation();
		TerrainMID->SetDoubleVectorParameterValue(TEXT("RoadDistOrigin"),
			FVector4(RoadDistCenter.X + Off.X, RoadDistCenter.Y + Off.Y, 0.0, 0.0));
		RoadDistJob.Reset();
		const double PublishMs = (FPlatformTime::Seconds() - PublishT0) * 1000.0;
		if (PublishMs > 4.0)
		{
			UE_LOG(LogTemp, Warning, TEXT("ODTerrainManager: road-dist publish took %.1f ms on the game thread"), PublishMs);
		}
	}
	if (FVector2D::Distance(FVector2D(Loc.X, Loc.Y), RoadDistCenter) < RoadDistRecenterCm)
	{
		return;
	}
	// texel-grid snap: a recenter never shifts the field sub-texel
	RoadDistJobCenter = FVector2D(FMath::GridSnap(Loc.X, (double)RoadDistTexelCm), FMath::GridSnap(Loc.Y, (double)RoadDistTexelCm));
	RoadDistJob = Async(EAsyncExecution::ThreadPool,
		[Hash = StationHash, C = RoadDistJobCenter]() { return BuildRoadDistTexels(*Hash, C); });
}

bool AODTerrainManager::IsInitialStreamingSettled(const FVector& AroundPos) const
{
	if (PendingTileBuilds.Num() > 0 || PendingCoarseTiles.Num() > 0 || PendingNearTiles.Num() > 0)
	{
		return false;   // parse or build work still queued somewhere
	}

	const FIntPoint Center(FMath::FloorToInt(AroundPos.X / TileSizeCm),
	                       FMath::FloorToInt(AroundPos.Y / TileSizeCm));
	const int32 Reach = FMath::CeilToInt(CollisionRadius / TileSizeCm);
	for (int32 dy = -Reach; dy <= Reach; ++dy)
	{
		for (int32 dx = -Reach; dx <= Reach; ++dx)
		{
			const FIntPoint Key = Center + FIntPoint(dx, dy);
			// no file at this key at all (MissingCoarseTiles), or a coarse
			// tile exists but there's no near variant to upgrade to
			// (MissingNearTiles) - either way there's nothing left to build,
			// doesn't count against readiness. See Tick()'s near-upgrade pass:
			// a key only ever reaches PendingNearTiles/Tiles-at-Near once its
			// coarse tile has resolved, so these two sets fully account for
			// "no near tile is coming."
			if (MissingCoarseTiles.Contains(Key) || MissingNearTiles.Contains(Key))
			{
				continue;
			}
			const FTerrainTile* Tile = Tiles.Find(Key);
			if (!Tile || Tile->Fidelity != ETileFidelity::Near)
			{
				return false;   // still waiting on this key
			}
		}
	}
	return true;
}

void AODTerrainManager::GetStreamingProgress(const FVector& AroundPos, int32& OutReady, int32& OutTotal) const
{
	OutReady = 0;
	OutTotal = 0;

	const FIntPoint Center(FMath::FloorToInt(AroundPos.X / TileSizeCm),
	                       FMath::FloorToInt(AroundPos.Y / TileSizeCm));
	const int32 Reach = FMath::CeilToInt(CollisionRadius / TileSizeCm);
	for (int32 dy = -Reach; dy <= Reach; ++dy)
	{
		for (int32 dx = -Reach; dx <= Reach; ++dx)
		{
			++OutTotal;
			const FIntPoint Key = Center + FIntPoint(dx, dy);
			if (MissingCoarseTiles.Contains(Key) || MissingNearTiles.Contains(Key))
			{
				++OutReady;
				continue;
			}
			const FTerrainTile* Tile = Tiles.Find(Key);
			if (Tile && Tile->Fidelity == ETileFidelity::Near)
			{
				++OutReady;
			}
		}
	}
}

void AODTerrainManager::LoadHorizon()
{
	if (!TerrainTilePrefix.StartsWith(TEXT("terrain_")))
	{
		return;
	}
	const double T0 = FPlatformTime::Seconds();
	PrewarmArea(TerrainTilePrefix);   // parsed + built on a worker (2026-10-22); waits only without a prewarm
	const TSharedPtr<const FAreaHorizon> H = GAreaCache.Horizon.GetResult();
	if (!H.IsValid())
	{
		UE_LOG(LogTemp, Display, TEXT("ODTerrainManager: no horizon mesh (horizon_%s.bin)"), *TerrainTilePrefix.Mid(8));
		return;
	}
	auto* Comp = NewObject<UDynamicMeshComponent>(this);
	Comp->SetMeshDrawPath(EDynamicMeshDrawPath::StaticDraw);   // built once: cached draw commands, not re-gathered every frame per view/shadow view
	Comp->SetupAttachment(RootComponent);
	Comp->SetRelativeLocation(FVector(H->Origin.X, H->Origin.Y, 0.0));   // FillTileMesh's local origin
	Comp->SetCollisionProfileName(TEXT("NoCollision"));
	Comp->SetCastShadow(false);   // 160+ km of low-res ground: not worth shadow-map pages
	Comp->RegisterComponent();
	Comp->SetMesh(FDynamicMesh3(H->Mesh));   // a copy: RetagHorizon edits this one's material ids
	if (TerrainMID)
	{
		Comp->SetMaterial(0, TerrainMID);
	}
	HorizonComp = Comp;
	UE_LOG(LogTemp, Display, TEXT("ODTerrainManager: horizon mesh %d verts, %d tris, %.1f ms on the game thread"),
		H->NumVerts, H->NumTris, (FPlatformTime::Seconds() - T0) * 1000.0);
}

void AODTerrainManager::RetagHorizon()
{
	// (2026-10-21, area picker) the horizon past a drawn area is never baked
	// in detail: slot 1 (UnsyncedMat) outside the outline and over cells the
	// runner hasn't baked yet. Re-run only when a status.json changed.
	const TSharedPtr<const TArray<FVector2D>, ESPMode::ThreadSafe> Outline = ODBakeLink::ActiveOutline();
	if (!HorizonComp || !UnsyncedMat || !Outline.IsValid())
	{
		return;
	}
	const FVector2D Origin(HorizonComp->GetComponentLocation());
	int32 NumOut = 0;
	HorizonComp->EditMesh([&](FDynamicMesh3& Mesh)
	{
		Mesh.Attributes()->EnableMaterialID();
		FDynamicMeshMaterialAttribute* Mat = Mesh.Attributes()->GetMaterialID();
		for (const int32 Tid : Mesh.TriangleIndicesItr())
		{
			const FVector3d C = Mesh.GetTriCentroid(Tid);
			const FVector2D P(C.X + Origin.X, C.Y + Origin.Y);
			const bool bOut = ODBakeLink::SignedDistanceCm(*Outline, P) < 0.0 || ODBakeLink::IsPointPending(P);
			Mat->SetValue(Tid, bOut ? 1 : 0);
			NumOut += bOut ? 1 : 0;
		}
	});
	HorizonComp->SetMaterial(1, UnsyncedMat);
	UE_LOG(LogTemp, Display, TEXT("ODTerrainManager: horizon retag - %d unsynced tris"), NumOut);
}

void AODTerrainManager::SpawnSyncIn(const UDynamicMeshComponent* Src)
{
	// (2026-10-21) a tile that was Pending lands under a copy of itself in the
	// unsynced look whose CustomPrimitiveData[0] = now makes M_ODUnsynced
	// dissolve it away over ~2 s - the "sync-in". Render-only, self-destroying.
	if (!UnsyncedMat || !Src || !GetWorld())
	{
		return;
	}
	auto* C = NewObject<UDynamicMeshComponent>(this);
	C->SetupAttachment(RootComponent);
	C->SetRelativeLocation(Src->GetRelativeLocation() + FVector(0.0, 0.0, 5.0));
	C->SetCollisionProfileName(TEXT("NoCollision"));
	C->SetCastShadow(false);
	C->RegisterComponent();
	C->SetMesh(FDynamicMesh3(*Src->GetMesh()));
	C->SetMaterial(0, UnsyncedMat);
	C->SetMaterial(1, UnsyncedMat);
	C->SetCustomPrimitiveDataFloat(0, GetWorld()->GetTimeSeconds());
	FTimerHandle Unused;
	GetWorldTimerManager().SetTimer(Unused, FTimerDelegate::CreateWeakLambda(C, [C]() { C->DestroyComponent(); }), 2.2f, false);
}

// OpenDrive.Terrain.ODT2 (2026-10-21): a two-triangle ODT2 tile built in
// memory parses to the verts/indices written and the derived UVs; a
// truncated copy and a wrong index width are both refused.
IMPLEMENT_SIMPLE_AUTOMATION_TEST(FODTerrainOdt2Check, "OpenDrive.Terrain.ODT2",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::ClientContext | EAutomationTestFlags::ProductFilter)

bool FODTerrainOdt2Check::RunTest(const FString& Parameters)
{
	const float P[4][3] = {{100000, 200000, 5}, {200000, 200000, 6}, {200000, 300000, 7}, {150000, 250000, 8}};
	const uint16 Idx[6] = {0, 1, 2, 0, 2, 3};
	const double U0 = 100000.0, V0 = 200000.0, Size = 100000.0;
	TArray<uint8> Raw;
	Raw.Append(reinterpret_cast<const uint8*>(P), sizeof(P));
	Raw.Append(reinterpret_cast<const uint8*>(Idx), sizeof(Idx));   // little-endian host, as the format
	int64 ZBytes = 0;
	FCompression::CompressMemoryBound(NAME_Zlib, ZBytes, Raw.Num());
	TArray<uint8> Z;
	Z.SetNumUninitialized((int32)ZBytes);
	TestTrue(TEXT("compress"), FCompression::CompressMemory(NAME_Zlib, Z.GetData(), ZBytes, Raw.GetData(), Raw.Num()));

	auto Put = [](TArray<uint8>& B, const void* Data, int32 N) { B.Append(static_cast<const uint8*>(Data), N); };
	TArray<uint8> Bin;
	Put(Bin, "ODT2", 4);
	Put(Bin, "0123456789abcdef", GenerationIdBytes);
	const uint8 Coll = 1, Flags = 1;
	const uint32 V = 4, T = 2, Z32 = (uint32)ZBytes;
	Put(Bin, &Coll, 1); Put(Bin, &Flags, 1); Put(Bin, &V, 4); Put(Bin, &T, 4);
	Put(Bin, &U0, 8); Put(Bin, &V0, 8); Put(Bin, &Size, 8); Put(Bin, &Z32, 4);
	Put(Bin, Z.GetData(), Z32);

	FParsedTerrainTile Out;
	if (!TestTrue(TEXT("parses"), AODTerrainManager::ParseTerrainTileBytes(Bin, Out)))
	{
		return false;
	}
	TestEqual(TEXT("gen id"), Out.GenerationId, FString(TEXT("0123456789abcdef")));
	TestTrue(TEXT("collision"), Out.bHasCollision);
	TestEqual(TEXT("verts"), Out.Verts.Num(), 12);
	TestEqual(TEXT("vert 2 z"), Out.Verts[8], 7.0f);
	TestEqual(TEXT("tris"), Out.Tris.Num(), 6);
	TestEqual(TEXT("tri 5"), (int32)Out.Tris[5], 3);
	TestEqual(TEXT("uv 2 u"), Out.Uvs[4], 1.0f);
	TestEqual(TEXT("uv 2 v"), Out.Uvs[5], 1.0f);
	TestEqual(TEXT("uv 3 u"), Out.Uvs[6], 0.5f);
	TestEqual(TEXT("uv 3 v"), Out.Uvs[7], 0.5f);

	TArray<uint8> Short = Bin;
	Short.SetNum(Short.Num() - 1);
	TestFalse(TEXT("truncated refused"), AODTerrainManager::ParseTerrainTileBytes(Short, Out));
	TArray<uint8> Wide = Bin;
	Wide[4 + GenerationIdBytes + 1] = 0;   // claims uint32 indices: inflated size no longer matches
	AddExpectedError(TEXT("Failed to uncompress memory"), EAutomationExpectedErrorFlags::Contains, 1);   // UncompressMemory logs it
	TestFalse(TEXT("index width mismatch refused"), AODTerrainManager::ParseTerrainTileBytes(Wide, Out));
	return true;
}
