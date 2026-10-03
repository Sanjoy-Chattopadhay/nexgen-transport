"""Pure derivation rules shared by the trip ingestion paths.

No I/O — just the arithmetic that turns raw TTA/CSV timestamps into the
analytics columns on `trips`, so the rules are stated once and can be tested
without a database.
"""


def journey_metrics(trip_start, ata_in, trip_km):
    """How long the truck actually ran -> (trip_duration_minutes, avg_speed_kmph).

    Measured from departure to ACTUAL ARRIVAL (`ata_in`) and nothing else.

    Measuring to the administrative closing stamp instead invents a journey
    that never happened: a duplicate trip voided with "NEW TRIP FOUND" hours
    after booking was recorded as a 936-minute run, and — wherever `trip_km`
    happened to be present — a speed to match. Those values then flowed into
    every route/driver/vehicle average and into the duration model's training
    labels.

    No arrival means the truck's journey time is UNKNOWN, so both values are
    None — never 0. `AVG()` skips NULL, so unknown trips drop out of every
    average; a 0 would have been averaged in and dragged the mean down.

    A negative interval (arrival before departure) is corrupt rather than
    unknown, and is likewise rejected instead of stored.
    """
    if not trip_start or not ata_in:
        return None, None
    minutes = (ata_in - trip_start).total_seconds() / 60
    if minutes < 0:
        return None, None
    duration = round(minutes, 2)
    avg_speed = None
    if trip_km and trip_km > 0 and duration > 0:
        avg_speed = round(trip_km / (duration / 60), 2)
    return duration, avg_speed


def eta_outcome(ata_in, trip_eta):
    """ETA compliance for one trip -> (eta_met, eta_delay_minutes).

    Judged against the ACTUAL ARRIVAL (`ata_in`) and nothing else.

    A trip's `trip_end` falls back to the administrative closing stamp when no
    arrival was ever recorded, and an administrative closure ("NEW TRIP FOUND",
    "TRIP CLOSE BY GRN") lands minutes after booking — comfortably inside a
    long-haul ETA. Judging against it scored trips that never ran as delivered
    on time, which is why this rule lives in one tested place.

    No arrival (or no ETA to compare against) means the outcome is UNKNOWN, so
    both values are None — never 0, which would read as "late". The OTD queries
    divide by COUNT(eta_met), so unknowns drop out of the rate rather than
    counting against the carrier.
    """
    if not ata_in or not trip_eta:
        return None, None
    eta_met = 1 if ata_in <= trip_eta else 0
    eta_delay = round(max(0.0, (ata_in - trip_eta).total_seconds() / 60), 2)
    return eta_met, eta_delay
