#include "OpenDriveUE.h"
#include "Modules/ModuleManager.h"
#include "Containers/Ticker.h"
#include "Engine/Engine.h"
#include "Engine/GameViewportClient.h"
#include "GameFramework/GameUserSettings.h"
#include "HAL/IConsoleManager.h"
#include "RenderTimer.h"
#include "RHIStats.h"
#include "DynamicRHI.h"
#include "ODBakeLink.h"
#include "ODTileSource.h"
#include "ODVisualStyle.h"
#if PLATFORM_WINDOWS
#include "Windows/AllowWindowsPlatformTypes.h"
#include <dxgi1_4.h>
#include "Windows/HideWindowsPlatformTypes.h"
#endif

namespace
{
/**
 * This process's VRAM use vs the OS budget, plus what spilled to shared system
 * memory, in MB (2026-10-22). Summit County ran at 70 ms GPU for ~2.5 min after
 * loading, then dropped to 22 ms with every pass (base pass, Lumen) slowing and
 * recovering together, which looks like VRAM oversubscription on the 8 GB card.
 * These numbers in the ODPerf line confirm or rule that out.
 */
bool QueryVram(float& UsedMb, float& BudgetMb, float& SharedMb)
{
#if PLATFORM_WINDOWS
	// the adapter with the most dedicated memory is the one UE renders on
	static IDXGIAdapter3* Adapter = []() -> IDXGIAdapter3*
	{
		IDXGIFactory1* Factory = nullptr;
		if (FAILED(CreateDXGIFactory1(__uuidof(IDXGIFactory1), (void**)&Factory)))
		{
			return nullptr;
		}
		IDXGIAdapter3* Best = nullptr;
		SIZE_T BestMem = 0;
		IDXGIAdapter1* A = nullptr;
		for (UINT I = 0; Factory->EnumAdapters1(I, &A) != DXGI_ERROR_NOT_FOUND; ++I)
		{
			DXGI_ADAPTER_DESC1 Desc;
			IDXGIAdapter3* A3 = nullptr;
			if (SUCCEEDED(A->GetDesc1(&Desc)) && Desc.DedicatedVideoMemory > BestMem
				&& SUCCEEDED(A->QueryInterface(__uuidof(IDXGIAdapter3), (void**)&A3)))
			{
				if (Best) { Best->Release(); }
				Best = A3;
				BestMem = Desc.DedicatedVideoMemory;
			}
			A->Release();
		}
		Factory->Release();
		return Best;
	}();
	DXGI_QUERY_VIDEO_MEMORY_INFO Local, Shared;
	if (Adapter && SUCCEEDED(Adapter->QueryVideoMemoryInfo(0, DXGI_MEMORY_SEGMENT_GROUP_LOCAL, &Local))
		&& SUCCEEDED(Adapter->QueryVideoMemoryInfo(0, DXGI_MEMORY_SEGMENT_GROUP_NON_LOCAL, &Shared)))
	{
		UsedMb = Local.CurrentUsage / 1048576.0f;
		BudgetMb = Local.Budget / 1048576.0f;
		SharedMb = Shared.CurrentUsage / 1048576.0f;
		return true;
	}
#endif
	return false;
}


/**
 * `stat unit` in the log (2026-10-16, perf pass): every 5 s one ODPerf line
 * with frame avg/p95/max and the game / render / RHI / GPU thread ms, draw
 * calls and RHI triangles, so a normal drive tells us where the frame goes
 * without anyone running the game for screenshots. Whichever of game / draw /
 * gpu is closest to the frame time is the bottleneck. Every 150 s (first at
 * 60 s) it also runs ProfileGPU, which prints the per-pass GPU tree (Lumen,
 * VSM, base pass, translucency, ...) into the log; passes under 1 % are cut.
 * -odperf=0 turns both off, -odprofilegpu=0 only the GPU tree.
 *
 * Hitches (2026-10-16): any frame over 2.5x the last window's median and
 * 40 ms logs one ODHitch line with that frame's game / draw / rhi / gpu ms
 * and the gap since the previous hitch, so the cadence and the guilty
 * thread show up. -odhitch also runs `stat dumphitches`: the engine then
 * logs the full game/render-thread stat tree (per-actor ticks included) of
 * every frame whose game or render thread passes t.HitchFrameTimeThreshold
 * (60 ms). Off by default: stat collection itself costs ~1 ms a frame.
 */
struct FPerfLog
{
	TArray<float> FrameMs;
	double WindowStart = 0.0, Started = 0.0, NextGpuDump = 60.0, LastHitch = 0.0;
	float MedianMs = 0.0f;
	int32 Hitches = 0;
	uint64 GameC = 0, RenderC = 0, RhiC = 0, GpuC = 0, Draws = 0, Prims = 0;
	bool bGpuDump = true, bStatHitches = false;

	bool Tick(float Dt)
	{
		const double Now = FPlatformTime::Seconds();
		if (Started == 0.0)
		{
			Started = WindowStart = Now;
			// log only (no profiler window), skip passes under 1 %; set here, not
			// in StartupModule, so the RHI module's cvars surely exist
			static const TCHAR* const Cvars[][2] = {
				{ TEXT("r.ProfileGPU.ShowUI"), TEXT("0") },
				{ TEXT("r.ProfileGPU.ThresholdPercent"), TEXT("1") },
				{ TEXT("r.ProfileGPU.UnicodeOutput"), TEXT("0") } };
			for (const auto& Kv : Cvars)
			{
				if (IConsoleVariable* V = IConsoleManager::Get().FindConsoleVariable(Kv[0]))
				{
					V->Set(Kv[1], ECVF_SetByCode);
				}
			}
			if (bStatHitches && GEngine)
			{
				GEngine->Exec(nullptr, TEXT("stat dumphitches -start"));
			}
			return true;
		}
		FrameMs.Add(Dt * 1000.0f);
		if (MedianMs > 0.0f && Dt * 1000.0f > FMath::Max(40.0f, 2.5f * MedianMs))
		{
			auto Ms1 = [](uint32 C) { return (float)FPlatformTime::ToMilliseconds(C); };
			UE_LOG(LogTemp, Display, TEXT("ODHitch: %.0f ms (median %.1f) | game %.1f draw %.1f rhi %.1f gpu %.1f ms | %.2f s since last"),
				Dt * 1000.0f, MedianMs, Ms1(GGameThreadTime), Ms1(GRenderThreadTime), Ms1(GRHIThreadTime), Ms1(RHIGetGPUFrameCycles(0)),
				LastHitch > 0.0 ? Now - LastHitch : 0.0);
			LastHitch = Now;
			++Hitches;
		}
		GameC += GGameThreadTime;
		RenderC += GRenderThreadTime;
		RhiC += GRHIThreadTime;
		GpuC += RHIGetGPUFrameCycles(0);
		Draws += GNumDrawCallsRHI[0];
		Prims += GNumPrimitivesDrawnRHI[0];
		if (Now - WindowStart >= 5.0 && FrameMs.Num() > 0)
		{
			Flush(Now);
		}
		if (bGpuDump && Now - Started >= NextGpuDump && GEngine && GEngine->GameViewport)
		{
			NextGpuDump += 150.0;
			GEngine->Exec(GEngine->GameViewport->GetWorld(), TEXT("ProfileGPU"));
		}
		return true;
	}

	void Flush(double Now)
	{
		const int32 N = FrameMs.Num();
		float Sum = 0.0f;
		for (float F : FrameMs) { Sum += F; }
		FrameMs.Sort();
		const float Avg = Sum / N;
		MedianMs = FrameMs[N / 2];
		auto Ms = [N](uint64 C) { return (float)FPlatformTime::ToMilliseconds64(C) / N; };
		FIntPoint Res(0, 0);
		if (GEngine && GEngine->GameViewport && GEngine->GameViewport->Viewport)
		{
			Res = GEngine->GameViewport->Viewport->GetSizeXY();
		}
		static IConsoleVariable* SP = IConsoleManager::Get().FindConsoleVariable(TEXT("r.ScreenPercentage"));
		const UGameUserSettings* GS = GEngine ? GEngine->GetGameUserSettings() : nullptr;
		float VramMb = -1.0f, BudgetMb = -1.0f, SharedMb = -1.0f;
		QueryVram(VramMb, BudgetMb, SharedMb);
		UE_LOG(LogTemp, Display, TEXT("ODPerf: %.1fs avg %.1f ms (%.0f fps) p95 %.1f max %.1f hitches %d | game %.1f draw %.1f rhi %.1f gpu %.1f ms | draws %llu tris %.2fM | %dx%d sp %.0f sg %d style %d | vram %.0f/%.0f MB shared %.0f MB"),
			Now - WindowStart, Avg, 1000.0f / FMath::Max(Avg, 0.01f), FrameMs[FMath::Min(N - 1, N * 95 / 100)], FrameMs.Last(), Hitches,
			Ms(GameC), Ms(RenderC), Ms(RhiC), Ms(GpuC), Draws / N, (double)Prims / N / 1.0e6,
			Res.X, Res.Y, SP ? SP->GetFloat() : 100.0f, GS ? GS->GetOverallScalabilityLevel() : -2, ODVisualStyle::Level(),
			VramMb, BudgetMb, SharedMb);
		FrameMs.Reset();
		Hitches = 0;
		GameC = RenderC = RhiC = GpuC = Draws = Prims = 0;
		WindowStart = Now;
	}
};

FPerfLog GPerfLog;
}

// Tile server setup runs here, before any level-placed manager's BeginPlay.
class FOpenDriveUEModule : public FDefaultGameModuleImpl
{
	virtual void StartupModule() override
	{
		ODTileSource::Init();
		ODBakeLink::Init();   // car.json heartbeat + "ready to drive" alert (2026-10-21)
		int32 Perf = 1, GpuDump = 1;
		FParse::Value(FCommandLine::Get(), TEXT("odperf="), Perf);
		FParse::Value(FCommandLine::Get(), TEXT("odprofilegpu="), GpuDump);
		if (Perf != 0 && !IsRunningCommandlet())
		{
			GPerfLog.bGpuDump = GpuDump != 0;
			GPerfLog.bStatHitches = FParse::Param(FCommandLine::Get(), TEXT("odhitch"));
			FTSTicker::GetCoreTicker().AddTicker(FTickerDelegate::CreateLambda([](float Dt) { return GPerfLog.Tick(Dt); }));
		}
	}
};

IMPLEMENT_PRIMARY_GAME_MODULE(FOpenDriveUEModule, OpenDriveUE, "OpenDriveUE");
