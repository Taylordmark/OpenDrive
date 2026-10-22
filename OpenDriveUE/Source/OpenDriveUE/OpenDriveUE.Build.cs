using UnrealBuildTool;

public class OpenDriveUE : ModuleRules
{
	public OpenDriveUE(ReadOnlyTargetRules Target) : base(Target)
	{
		PCHUsage = PCHUsageMode.UseExplicitOrSharedPCHs;

		PublicDependencyModuleNames.AddRange(new string[]
		{
			"Core",
			"CoreUObject",
			"Engine",
			"InputCore",
			"EnhancedInput",
			"PhysicsCore",
			"Chaos",   // FRigidBodyHandle_Internal - the car sim's fixed-step body (ODVehicleMovement)
			"Landscape",
			"Foliage",
			"AudioMixer",
			"GeometryFramework",
			"GeometryCore",
			// ODFarForest.cpp's runtime-built canopy card mesh (BuildFromMeshDescriptions)
			"MeshDescription",
			"StaticMeshDescription",
			"AssetRegistry",
			// FDisplayMetrics (Settings menu's "Native" resolution row, Arc 4.4) -
			// lives in ApplicationCore, not Engine/Core.
			"ApplicationCore",
			// FSlateFontInfo / GWhiteTexture - menu panel text at real pixel
			// size and arrow triangles (ODRaceHUD::DrawTabbedPanel).
			"SlateCore",
			// SEditableTextBox - the New Area name field (ODAreaDraw, 2026-10-21)
			"Slate",
			"RenderCore",
			// GNumDrawCallsRHI / RHIGetGPUFrameCycles (OpenDriveUE.cpp perf log).
			"RHI",
			// climate_<area>_test.json (ODWeatherController, Weather W1).
			"Json",
			// ODTileSource: -ODTileServer live tile fetch.
			"HTTP",
			// ODBakeLink: "ready to drive" toast (FSlateNotificationManager, 2026-10-21).
			"Slate"
		});

		// ODWheelDevice: racing wheel input + force feedback (Arc 6)
		if (Target.Platform == UnrealTargetPlatform.Win64)
		{
			PublicSystemLibraries.AddRange(new string[] { "dinput8.lib", "dxguid.lib" });
		}
	}
}
