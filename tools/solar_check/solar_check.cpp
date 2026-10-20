// ODSolar.h vs NOAA's solar calculator (gml.noaa.gov/grad/solcalc, its own
// calcAzEl() run 2026-10-20) at Gasquet CA (far_gasquet in worldmap.json).
// Build + run: powershell -File tools/solar_check/run.ps1
#include <cstdio>
#include <cmath>
#include "../../OpenDriveUE/Source/OpenDriveUE/ODSolar.h"

int main()
{
	const double Lat = 41.8475, Lon = -123.965;
	struct { const char* Date; double Doy, Hour, Noaa_El, Noaa_Az; } Cases[] = {
		{"Jun 21", 172, 12.0, 65.4946, 132.6183},
		{"Jun 21", 172, 20.0, 8.1705, 294.3540},
		{"Dec 21", 355, 12.0, 24.6681, 176.4414},
		{"Dec 21", 355, 20.0, -34.7142, 268.3399},
	};
	int Fails = 0;
	for (const auto& C : Cases)
	{
		const double Utc = ODSolar::IsUSDaylightTime(C.Doy) ? -7.0 : -8.0;
		double El, Az;
		ODSolar::SunPosition(Lat, Lon, C.Doy, C.Hour, Utc, El, Az);
		// 0.3 deg: Spencer series + no refraction (NOAA's adds ~0.1 deg at 8 deg up)
		const bool Ok = std::fabs(El - C.Noaa_El) < 0.3 && std::fabs(Az - C.Noaa_Az) < 0.3;
		Fails += !Ok;
		std::printf("%s %05.2f UTC%+.0f  el %7.2f (NOAA %7.2f)  az %7.2f (NOAA %7.2f)  %s\n",
			C.Date, C.Hour, Utc, El, C.Noaa_El, Az, C.Noaa_Az, Ok ? "ok" : "FAIL");
	}
	// NOAA: Jun 21 sunset 20:54:31 PDT (sun centre at -0.833 deg)
	double El, Az, Set = 0.0;
	for (double H = 18.0; H < 23.0; H += 1.0 / 3600.0)
	{
		ODSolar::SunPosition(Lat, Lon, 172, H, -7.0, El, Az);
		if (El < -0.833) { Set = H; break; }
	}
	const bool SetOk = std::fabs(Set - (20.0 + 54.0 / 60.0 + 31.0 / 3600.0)) < 2.0 / 60.0;
	Fails += !SetOk;
	std::printf("Jun 21 sunset %02d:%02d PDT (NOAA 20:54)  %s\n", int(Set), int(std::fmod(Set, 1.0) * 60.0), SetOk ? "ok" : "FAIL");
	return Fails;
}
