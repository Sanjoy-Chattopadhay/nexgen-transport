"""Uploaded trips: a spreadsheet of GPS in, every pipeline step shown, KPIs out.

Separate from the run ledger on purpose. An upload is somebody checking the
engine, or analysing a trip the fleet system never opened; it must never mix
into `geo_visit` / `geo_trip_summary` and move a published number.

    parse   the sheet -> fixes, and which column was read as what
    trace   the same engine as a batch run, instrumented so every decision
            it makes is recorded with the numbers it made it on
    store   uploads, their fixes, and one compressed document per step
    sample  a real long trip out of the feed, as a spreadsheet to upload
"""
