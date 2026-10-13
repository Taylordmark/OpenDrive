#pragma once

#include "CoreMinimal.h"

class UObject;

/** Where a streamed tile sidecar comes from (live-streaming step 1).
 *
 *  Order: the baked file under Content/OpenDrive/ (unchanged behavior, so a
 *  shipped/prebaked area never touches the network) -> Saved/TileCache/ ->
 *  HTTP GET <server>/<path relative to Content/OpenDrive/>, written into the
 *  cache. The server is only used when the game is launched with
 *  -ODTileServer=http://host:port/ ; without it a missing file is Missing,
 *  exactly as before.
 *
 *  Server contract: 200 = tile bytes, 404 = tile is genuinely empty (the
 *  manager's MissingTiles semantics), 202 / any error / timeout = not baked
 *  yet - the manager retries after RetryDelaySeconds instead of marking the
 *  tile empty forever. `python -m http.server -d Content/OpenDrive 8765`
 *  already satisfies this for prebaked tiles. */
enum class EODTileSource : uint8
{
	Ready,     // OutReadPath is a local file to parse
	Missing,   // no tile here - treat as empty
	Pending,   // ask again later
};

namespace ODTileSource
{
	constexpr float RetryDelaySeconds = 2.0f;

	/** Game thread, once (AODGameMode::BeginPlay): reads -ODTileServer and
	 *  loads the HTTP module so worker threads never have to. */
	void Init();

	/** Worker thread - may block on HTTP for up to ~30 s. ContentPath is the
	 *  usual FPaths::ProjectContentDir() / "OpenDrive/..." sidecar path. */
	EODTileSource Resolve(const FString& ContentPath, FString& OutReadPath);

	/** Game thread: removes Key from Pending after RetryDelaySeconds so the
	 *  owner's Tick re-requests it - only if the tile is still wanted by then. */
	void RetryLater(UObject* Owner, TSet<FIntPoint>& Pending, const FIntPoint& Key);
}
