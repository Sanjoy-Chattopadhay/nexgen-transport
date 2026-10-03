-- Two things the source sent that the first import did not keep exactly.
-- Found by verify_trip_copies on 3 October 2026: 51 of 7,257 trips rebuilt
-- with the right fixes but not the right text or totals.
--
-- 1. Waypoint names exactly as the feed spells them. The feed carries both
--    'Ramgarh' and 'RAMGARH' (and 'NON_METALLIC_...' beside 'Non_Metallic_...'),
--    for different fixes; a case-insensitive key merged each pair into
--    whichever spelling arrived first, so 43 trips showed the other spelling.
--    The dictionary is now keyed on the exact bytes. The contract views hand
--    the names back with the feed's own case-insensitive collation, so
--    grouping and comparing behave exactly as they did in Smart-Truck.
ALTER TABLE ref_waypoint
    MODIFY s_name       VARCHAR(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
    MODIFY s_state_abbr VARCHAR(10)  CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL DEFAULT '';

-- 2. The upstream's own cumulative distance where it is not the running sum
--    of the per-fix distances. A trip's i_cdist is derived as that running
--    sum, which is what the source sends -- except in 8 trips where its
--    counter restarted once mid-trip (19,449 m then 6,209 m on the next fix).
--    Smart-Truck stored and showed the source's figure, so it is kept here as
--    a per-trip exception, NULL where the derived sum already agrees.
ALTER TABLE trip_fix_override
    ADD COLUMN i_cdist_m INT NULL AFTER i_dist_m;
