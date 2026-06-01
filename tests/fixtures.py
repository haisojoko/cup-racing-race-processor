"""Fixtures mimicking AC dedicated-server result JSON structure."""

QUALIFYING_RESULT = {
    "TrackName": "imola",
    "TrackConfig": "",
    "Type": "QUALIFY",
    "Laps": [
        {"DriverName": "Josie", "CarModel": "tatuus_fa01", "LapTime": 97500, "Sectors": [31200, 33100, 33200], "Cuts": 0, "Timestamp": 120000},
        {"DriverName": "Josie", "CarModel": "tatuus_fa01", "LapTime": 96800, "Sectors": [31000, 32800, 33000], "Cuts": 0, "Timestamp": 220000},
        {"DriverName": "Toby", "CarModel": "tatuus_fa01", "LapTime": 98200, "Sectors": [31800, 33200, 33200], "Cuts": 0, "Timestamp": 125000},
        {"DriverName": "Toby", "CarModel": "tatuus_fa01", "LapTime": 97100, "Sectors": [31400, 32900, 32800], "Cuts": 0, "Timestamp": 225000},
        {"DriverName": "Lee", "CarModel": "tatuus_fa01", "LapTime": 98800, "Sectors": [32000, 33500, 33300], "Cuts": 0, "Timestamp": 130000},
        {"DriverName": "Lee", "CarModel": "tatuus_fa01", "LapTime": 97900, "Sectors": [31600, 33200, 33100], "Cuts": 0, "Timestamp": 230000},
    ],
    "Result": [
        {"DriverName": "Josie", "CarModel": "tatuus_fa01", "BestLap": 96800, "TotalTime": 194300, "BallastKG": 0, "Restrictor": 0},
        {"DriverName": "Toby", "CarModel": "tatuus_fa01", "BestLap": 97100, "TotalTime": 195300, "BallastKG": 0, "Restrictor": 0},
        {"DriverName": "Lee", "CarModel": "tatuus_fa01", "BestLap": 97900, "TotalTime": 196700, "BallastKG": 0, "Restrictor": 0},
    ],
    "Events": [],
}

RACE_RESULT = {
    "TrackName": "imola",
    "TrackConfig": "",
    "Type": "RACE",
    "Laps": [
        # Lap 1
        {"DriverName": "Josie", "CarModel": "tatuus_fa01", "LapTime": 99000, "Sectors": [32000, 33500, 33500], "Cuts": 0, "Timestamp": 99000},
        {"DriverName": "Toby", "CarModel": "tatuus_fa01", "LapTime": 99500, "Sectors": [32200, 33600, 33700], "Cuts": 0, "Timestamp": 99500},
        {"DriverName": "Lee", "CarModel": "tatuus_fa01", "LapTime": 100200, "Sectors": [32500, 34000, 33700], "Cuts": 0, "Timestamp": 100200},
        # Lap 2
        {"DriverName": "Josie", "CarModel": "tatuus_fa01", "LapTime": 97200, "Sectors": [31100, 33000, 33100], "Cuts": 0, "Timestamp": 196200},
        {"DriverName": "Toby", "CarModel": "tatuus_fa01", "LapTime": 97000, "Sectors": [31000, 32900, 33100], "Cuts": 0, "Timestamp": 196500},
        {"DriverName": "Lee", "CarModel": "tatuus_fa01", "LapTime": 98000, "Sectors": [31500, 33200, 33300], "Cuts": 0, "Timestamp": 198200},
        # Lap 3
        {"DriverName": "Josie", "CarModel": "tatuus_fa01", "LapTime": 97400, "Sectors": [31200, 33100, 33100], "Cuts": 0, "Timestamp": 293600},
        {"DriverName": "Toby", "CarModel": "tatuus_fa01", "LapTime": 97100, "Sectors": [31100, 33000, 33000], "Cuts": 0, "Timestamp": 293600},
        {"DriverName": "Lee", "CarModel": "tatuus_fa01", "LapTime": 97800, "Sectors": [31400, 33100, 33300], "Cuts": 0, "Timestamp": 296000},
        # Lap 4
        {"DriverName": "Josie", "CarModel": "tatuus_fa01", "LapTime": 97100, "Sectors": [31000, 33000, 33100], "Cuts": 0, "Timestamp": 390700},
        {"DriverName": "Toby", "CarModel": "tatuus_fa01", "LapTime": 97300, "Sectors": [31100, 33100, 33100], "Cuts": 0, "Timestamp": 390900},
        {"DriverName": "Lee", "CarModel": "tatuus_fa01", "LapTime": 97600, "Sectors": [31300, 33100, 33200], "Cuts": 0, "Timestamp": 393600},
    ],
    "Result": [
        {"DriverName": "Josie", "CarModel": "tatuus_fa01", "BestLap": 97100, "TotalTime": 390700, "BallastKG": 0, "Restrictor": 0},
        {"DriverName": "Toby", "CarModel": "tatuus_fa01", "BestLap": 97000, "TotalTime": 390900, "BallastKG": 0, "Restrictor": 0},
        {"DriverName": "Lee", "CarModel": "tatuus_fa01", "BestLap": 97600, "TotalTime": 393600, "BallastKG": 0, "Restrictor": 0},
    ],
    "Events": [
        {
            "Type": "COLLISION_WITH_CAR",
            "Driver": {"Name": "Josie", "Guid": "", "Team": "", "Nation": ""},
            "OtherDriver": {"Name": "Toby", "Guid": "", "Team": "", "Nation": ""},
            "ImpactSpeed": 14.2,
            "WorldPosition": {"X": 100.5, "Y": 1.2, "Z": -340.0},
            "RelPosition": {"X": 0.5, "Y": 0.0, "Z": -1.2},
            "Timestamp": 295000,
        },
        {
            "Type": "COLLISION_WITH_ENV",
            "Driver": {"Name": "Lee", "Guid": "", "Team": "", "Nation": ""},
            "ImpactSpeed": 5.1,
            "WorldPosition": {"X": 200.0, "Y": 0.0, "Z": -100.0},
            "RelPosition": {"X": 0.0, "Y": 0.0, "Z": 0.0},
        },
    ],
}
