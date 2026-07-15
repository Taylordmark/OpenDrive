using UnrealBuildTool;
using System.Collections.Generic;

public class OpenDriveUETarget : TargetRules
{
	public OpenDriveUETarget(TargetInfo Target) : base(Target)
	{
		Type = TargetType.Game;
		DefaultBuildSettings = BuildSettingsVersion.Latest;
		IncludeOrderVersion = EngineIncludeOrderVersion.Latest;
		ExtraModuleNames.Add("OpenDriveUE");
	}
}
