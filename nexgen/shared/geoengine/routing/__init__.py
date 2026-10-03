"""Routes, deviations and what they cost.

    geometry   a route as a polyline: distance of a fix from it, and how far along it
    plan       the route a trip should have taken (OSRM, or learned from the lane's history)
    deviation  when a truck left that route, for how long, and why -- detection and rerouting
    economics  extra kilometres and hours as rupees: loss or saving against the plan
    analysis   all of it for a run's trips, stored per trip and per deviation
"""
