"""Seed fitment data — demo dataset used until the live crawl database lands.

IMPORTANT: These are common bulb sizes from general knowledge, NOT verified
against Sylvania's guide. The user must QA this data (or replace it with the
crawl output in ../fitment/) before selling against it.

Schema mirrors the crawl worker's output:
  vehicles: vehicle_id, year, make, model, trim
  fitment:  vehicle_id, position (canonical snake_case), bulb_size_raw,
            bulb_size (canonical), note
"""

SEED_VEHICLES = [
    {
        "vehicle_id": "seed-001", "year": 2018, "make": "Toyota",
        "model": "Camry", "trim": None,
        "fitment": [
            {"position": "low_beam", "bulb_size": "H11"},
            {"position": "high_beam", "bulb_size": "9005"},
            {"position": "fog_light", "bulb_size": "H11"},
            {"position": "front_turn_signal", "bulb_size": "7440"},
            {"position": "rear_turn_signal", "bulb_size": "7440"},
            {"position": "brake_light", "bulb_size": "7440"},
            {"position": "reverse_light", "bulb_size": "921"},
            {"position": "license_plate", "bulb_size": "168"},
            {"position": "dome_light", "bulb_size": "194"},
        ],
    },
    {
        "vehicle_id": "seed-002", "year": 2016, "make": "Honda",
        "model": "Civic", "trim": None,
        "fitment": [
            {"position": "low_beam", "bulb_size": "H11"},
            {"position": "high_beam", "bulb_size": "9005"},
            {"position": "fog_light", "bulb_size": "H11"},
            {"position": "front_turn_signal", "bulb_size": "7440"},
            {"position": "rear_turn_signal", "bulb_size": "7440"},
            {"position": "brake_light", "bulb_size": "7443"},
            {"position": "reverse_light", "bulb_size": "921"},
            {"position": "license_plate", "bulb_size": "168"},
            {"position": "dome_light", "bulb_size": "194"},
        ],
    },
    {
        "vehicle_id": "seed-003", "year": 2019, "make": "Ford",
        "model": "F-150", "trim": None,
        "fitment": [
            {"position": "low_beam", "bulb_size": "H11"},
            {"position": "high_beam", "bulb_size": "9005"},
            {"position": "fog_light", "bulb_size": "9145"},
            {"position": "front_turn_signal", "bulb_size": "3157"},
            {"position": "rear_turn_signal", "bulb_size": "3157"},
            {"position": "brake_light", "bulb_size": "3157"},
            {"position": "reverse_light", "bulb_size": "921"},
            {"position": "license_plate", "bulb_size": "194"},
            {"position": "dome_light", "bulb_size": "194"},
        ],
    },
    {
        "vehicle_id": "seed-004", "year": 2018, "make": "Chevrolet",
        "model": "Silverado 1500", "trim": None,
        "fitment": [
            {"position": "low_beam", "bulb_size": "H11"},
            {"position": "high_beam", "bulb_size": "9005"},
            {"position": "fog_light", "bulb_size": "5202"},
            {"position": "front_turn_signal", "bulb_size": "3157"},
            {"position": "rear_turn_signal", "bulb_size": "3157"},
            {"position": "brake_light", "bulb_size": "3157"},
            {"position": "reverse_light", "bulb_size": "921"},
            {"position": "license_plate", "bulb_size": "194"},
            {"position": "dome_light", "bulb_size": "194"},
        ],
    },
    {
        "vehicle_id": "seed-005", "year": 2020, "make": "Toyota",
        "model": "RAV4", "trim": None,
        "fitment": [
            {"position": "low_beam", "bulb_size": "H11"},
            {"position": "high_beam", "bulb_size": "9005"},
            {"position": "fog_light", "bulb_size": "H11"},
            {"position": "front_turn_signal", "bulb_size": "7440"},
            {"position": "rear_turn_signal", "bulb_size": "7440"},
            {"position": "brake_light", "bulb_size": "7440"},
            {"position": "reverse_light", "bulb_size": "921"},
            {"position": "license_plate", "bulb_size": "168"},
            {"position": "dome_light", "bulb_size": "194"},
        ],
    },
    {
        "vehicle_id": "seed-006", "year": 2017, "make": "Toyota",
        "model": "Corolla", "trim": None,
        "fitment": [
            {"position": "low_beam", "bulb_size": "H11"},
            {"position": "high_beam", "bulb_size": "9005"},
            {"position": "fog_light", "bulb_size": "H11"},
            {"position": "front_turn_signal", "bulb_size": "7440"},
            {"position": "rear_turn_signal", "bulb_size": "7440"},
            {"position": "brake_light", "bulb_size": "7443"},
            {"position": "reverse_light", "bulb_size": "921"},
            {"position": "license_plate", "bulb_size": "168"},
            {"position": "dome_light", "bulb_size": "194"},
        ],
    },
    {
        "vehicle_id": "seed-007", "year": 2018, "make": "Honda",
        "model": "Accord", "trim": None,
        "fitment": [
            {"position": "low_beam", "bulb_size": "H11"},
            {"position": "high_beam", "bulb_size": "9005"},
            {"position": "front_turn_signal", "bulb_size": "7440"},
            {"position": "rear_turn_signal", "bulb_size": "7440"},
            {"position": "brake_light", "bulb_size": "7443"},
            {"position": "reverse_light", "bulb_size": "921"},
            {"position": "license_plate", "bulb_size": "168"},
            {"position": "dome_light", "bulb_size": "194"},
        ],
    },
    {
        "vehicle_id": "seed-008", "year": 2019, "make": "Nissan",
        "model": "Altima", "trim": None,
        "fitment": [
            {"position": "low_beam", "bulb_size": "H11"},
            {"position": "high_beam", "bulb_size": "9005"},
            {"position": "fog_light", "bulb_size": "H11"},
            {"position": "front_turn_signal", "bulb_size": "7440"},
            {"position": "rear_turn_signal", "bulb_size": "7440"},
            {"position": "brake_light", "bulb_size": "7440"},
            {"position": "reverse_light", "bulb_size": "921"},
            {"position": "license_plate", "bulb_size": "168"},
            {"position": "dome_light", "bulb_size": "194"},
        ],
    },
    {
        "vehicle_id": "seed-009", "year": 2020, "make": "Toyota",
        "model": "Tacoma", "trim": None,
        "fitment": [
            {"position": "low_beam", "bulb_size": "H11"},
            {"position": "high_beam", "bulb_size": "9005"},
            {"position": "fog_light", "bulb_size": "9145"},
            {"position": "front_turn_signal", "bulb_size": "7440"},
            {"position": "rear_turn_signal", "bulb_size": "7440"},
            {"position": "brake_light", "bulb_size": "7440"},
            {"position": "reverse_light", "bulb_size": "921"},
            {"position": "license_plate", "bulb_size": "168"},
            {"position": "dome_light", "bulb_size": "194"},
        ],
    },
    {
        "vehicle_id": "seed-010", "year": 2018, "make": "Jeep",
        "model": "Wrangler", "trim": None,
        "fitment": [
            {"position": "low_beam", "bulb_size": "H13"},
            {"position": "high_beam", "bulb_size": "H13",
             "note": "Dual-filament H13 serves low and high beam"},
            {"position": "fog_light", "bulb_size": "5202"},
            {"position": "front_turn_signal", "bulb_size": "3157"},
            {"position": "rear_turn_signal", "bulb_size": "3157"},
            {"position": "brake_light", "bulb_size": "3157"},
            {"position": "reverse_light", "bulb_size": "921"},
            {"position": "license_plate", "bulb_size": "194"},
            {"position": "dome_light", "bulb_size": "194"},
        ],
    },
]
