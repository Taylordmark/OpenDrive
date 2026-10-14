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
			"AssetRegistry",
			// FDisplayMetrics (Settings menu's "Native" resolution row, Arc 4.4) -
			// lives in ApplicationCore, not Engine/Core.
			"ApplicationCore",
			// FSlateFontInfo / GWhiteTexture - menu panel text at real pixel
			// size and arrow triangles (ODRaceHUD::DrawTabbedPanel).
			"SlateCore",
			// SWebBrowser (CEF) - the Open Drive / Track Select globe, Content/OpenDrive/WorldMap
			"Slate",
			"WebBrowser",
			"RenderCore",
			// climate_<area>_test.json (ODWeatherController, Weather W1).
			"Json",
			// ODTileSource: -ODTileServer live tile fetch.
			"HTTP"
		});
	}
}
