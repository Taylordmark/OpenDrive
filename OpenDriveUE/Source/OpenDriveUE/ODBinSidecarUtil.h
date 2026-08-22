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
