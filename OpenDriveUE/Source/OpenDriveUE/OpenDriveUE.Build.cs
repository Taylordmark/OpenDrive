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
			"RenderCore",
			// climate_<area>_test.json (ODWeatherController, Weather W1).
			"Json"
		});
	}
}
