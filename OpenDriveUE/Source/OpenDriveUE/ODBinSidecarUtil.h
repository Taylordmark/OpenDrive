#pragma once

#include "CoreMinimal.h"

/** Manual little-endian parsing helpers shared by every binary sidecar
 *  reader in this project (ODT1/ODR2/ODW3/ODU1/etc.) - no struct cast, no
 *  packing/alignment assumption on disk. Consolidated 2026-07-28: four
 *  managers (Road/Terrain/Barrier/Building) each independently defined an
 *  identical anonymous-namespace copy of these two functions, which
 *  compiled fine under the Editor target's per-file (non-unity) build but
 *  hard-failed the standalone Game target's unity build - unity merges
 *  several of those .cpp files into one translation unit, and an anonymous
 *  namespace is unique per TU, not per source file, so the four identical
 *  ReadU32LE/ReadF32LE definitions collided as soon as they landed in the
 *  same unity blob. `inline` functions in a shared header are exempt from
 *  the one-definition-rule problem that caused that (safe to include from
 *  any number of translation units, unity-merged or not) - and it deletes
 *  three of the four duplicate copies as a bonus. */

inline uint32 ReadU32LE(const uint8* P)
{
	return (uint32)P[0] | (uint32)P[1] << 8 | (uint32)P[2] << 16 | (uint32)P[3] << 24;
}

inline float ReadF32LE(const uint8* P)
{
	float V;
	FMemory::Memcpy(&V, P, 4);
	return V;
}

/** Fixed-width ASCII-hex generation-id field every sidecar header now
 *  carries immediately after its magic (pipeline/generation_id.py,
 *  2026-08-21, AUDIT-RELIABILITY.md SS3.1/SS5.1) - a short hash of
 *  road_stations.json's content plus the constants that affect derived
 *  geometry shape. Lets a tile manager notice when tiles it's streaming
 *  disagree about which pipeline run they came from (e.g. barrier tiles
 *  baked against a stale road_stations.json next to fresh terrain tiles -
 *  exactly SS2's mixed-bake bug) without making that mismatch fatal: see
 *  each manager's GenerationMismatchWarned set for the "log once per
 *  prefix, never block" policy this is meant to serve. */
inline constexpr int32 GenerationIdBytes = 16;

/** Reads the GenerationIdBytes ASCII bytes at P into a plain FString. Does
 *  NOT bounds-check P/P+GenerationIdBytes against the buffer - callers must
 *  confirm Remaining() >= GenerationIdBytes first, same convention as
 *  ReadU32LE/ReadF32LE above. */
inline FString ReadGenerationId(const uint8* P)
{
	return FString(GenerationIdBytes, reinterpret_cast<const ANSICHAR*>(P));
}
