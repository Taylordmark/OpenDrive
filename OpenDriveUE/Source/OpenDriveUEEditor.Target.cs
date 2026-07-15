using UnrealBuildTool;
using System.Collections.Generic;

public class OpenDriveUEEditorTarget : TargetRules
{
	public OpenDriveUEEditorTarget(TargetInfo Target) : base(Target)
	{
		Type = TargetType.Editor;
		DefaultBuildSettings = BuildSettingsVersion.Latest;
		IncludeOrderVersion = EngineIncludeOrderVersion.Latest;
		ExtraModuleNames.Add("OpenDriveUE");
	}
}
