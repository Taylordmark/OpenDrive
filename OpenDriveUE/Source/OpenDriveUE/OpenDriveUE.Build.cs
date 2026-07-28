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
			"AssetRegistry"
		});
	}
}
