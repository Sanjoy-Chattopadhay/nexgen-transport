-- nx_geo: the geofencing engine's tables, exactly as Geo-Fencing's live
-- schema had them (read from SHOW CREATE TABLE, all its ALTERs applied),
-- plus Smart-Truck's circle-geofence tables. The feed copies (geo_gps_ping,
-- geo_trip, geo_trip_meta, geo_vehicle, geo_fleet_sync) are gone: those names
-- are alias views over the fleet service's published views (R__aliases.sql).


-- geofencing.geo_site
CREATE TABLE IF NOT EXISTS `geo_site` (
  `i_site_id` int NOT NULL,
  `i_entity_id` int DEFAULT NULL,
  `s_site_name` varchar(255) NOT NULL,
  `s_type` varchar(40) DEFAULT NULL,
  `s_category` varchar(16) NOT NULL DEFAULT 'normal',
  `i_max_speed` smallint DEFAULT NULL,
  `i_tolerance` int NOT NULL DEFAULT '0',
  `b_active` tinyint(1) NOT NULL DEFAULT '1',
  `dt_created` datetime(6) DEFAULT NULL,
  `s_created_by` varchar(64) DEFAULT NULL,
  `dt_modified` datetime(6) DEFAULT NULL,
  `s_modified_by` varchar(64) DEFAULT NULL,
  `dt_loaded` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`i_site_id`),
  KEY `idx_site_active` (`b_active`,`s_type`),
  KEY `idx_site_entity` (`i_entity_id`),
  KEY `idx_site_category` (`s_category`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- geofencing.geo_site_vertex
CREATE TABLE IF NOT EXISTS `geo_site_vertex` (
  `i_site_id` int NOT NULL,
  `i_seq` int NOT NULL,
  `d_lat` decimal(10,8) NOT NULL,
  `d_long` decimal(11,8) NOT NULL,
  PRIMARY KEY (`i_site_id`,`i_seq`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- geofencing.geo_fence
CREATE TABLE IF NOT EXISTS `geo_fence` (
  `i_fence_id` int NOT NULL AUTO_INCREMENT,
  `i_site_id` int NOT NULL,
  `s_site_name` varchar(255) NOT NULL,
  `s_type` varchar(40) DEFAULT NULL,
  `s_category` varchar(16) NOT NULL DEFAULT 'normal',
  `i_max_speed` smallint DEFAULT NULL,
  `i_tolerance` int NOT NULL DEFAULT '0',
  `b_active` tinyint(1) NOT NULL DEFAULT '1',
  `i_vertices` int NOT NULL,
  `d_min_lat` decimal(10,8) NOT NULL,
  `d_max_lat` decimal(10,8) NOT NULL,
  `d_min_long` decimal(11,8) NOT NULL,
  `d_max_long` decimal(11,8) NOT NULL,
  `d_centroid_lat` decimal(10,8) NOT NULL,
  `d_centroid_long` decimal(11,8) NOT NULL,
  `d_area_sqm` double NOT NULL,
  `d_perimeter_m` double NOT NULL,
  `b_self_intersecting` tinyint(1) NOT NULL DEFAULT '0',
  `s_geom_notes` varchar(255) DEFAULT NULL,
  `g_poly` polygon NOT NULL /*!80003 SRID 0 */,
  `dt_compiled` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `d_inradius_m` float DEFAULT NULL,
  `s_state` varchar(64) DEFAULT NULL,
  `s_district` varchar(96) DEFAULT NULL,
  `s_states` varchar(255) DEFAULT NULL,
  `d_state_offset_m` float DEFAULT NULL,
  PRIMARY KEY (`i_fence_id`),
  UNIQUE KEY `uq_fence_site` (`i_site_id`),
  KEY `idx_fence_active` (`b_active`),
  KEY `idx_fence_category` (`s_category`,`b_active`),
  SPATIAL KEY `sidx_fence_poly` (`g_poly`),
  KEY `idx_fence_state` (`s_state`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- geofencing.geo_import_run
CREATE TABLE IF NOT EXISTS `geo_import_run` (
  `i_import_id` int NOT NULL AUTO_INCREMENT,
  `dt_started` datetime(6) NOT NULL,
  `dt_finished` datetime(6) DEFAULT NULL,
  `s_source` varchar(512) NOT NULL,
  `i_sites_read` int DEFAULT '0',
  `i_sites_loaded` int DEFAULT '0',
  `i_vertices_read` int DEFAULT '0',
  `i_vertices_loaded` int DEFAULT '0',
  `i_fences_built` int DEFAULT '0',
  `i_rejected` int DEFAULT '0',
  `s_status` varchar(16) NOT NULL DEFAULT 'running',
  `s_error` text,
  `j_summary` json DEFAULT NULL,
  PRIMARY KEY (`i_import_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- geofencing.geo_import_reject
CREATE TABLE IF NOT EXISTS `geo_import_reject` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `i_import_id` int NOT NULL,
  `s_entity` varchar(32) NOT NULL,
  `i_site_id` int DEFAULT NULL,
  `s_reason` varchar(64) NOT NULL,
  `s_detail` varchar(512) DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_rej_import` (`i_import_id`,`s_entity`),
  KEY `idx_rej_site` (`i_site_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- geofencing.geo_run
CREATE TABLE IF NOT EXISTS `geo_run` (
  `i_run_id` int NOT NULL AUTO_INCREMENT,
  `dt_started` datetime(6) NOT NULL,
  `dt_finished` datetime(6) DEFAULT NULL,
  `s_mode` varchar(16) NOT NULL,
  `s_scope` varchar(255) DEFAULT NULL,
  `dt_from` datetime DEFAULT NULL,
  `dt_to` datetime DEFAULT NULL,
  `i_trips` int DEFAULT '0',
  `i_pings_read` bigint DEFAULT '0',
  `i_pings_used` bigint DEFAULT '0',
  `i_pings_dropped` bigint DEFAULT '0',
  `i_events` int DEFAULT '0',
  `i_visits` int DEFAULT '0',
  `i_violations` int DEFAULT '0',
  `d_seconds` double DEFAULT NULL,
  `d_pings_per_sec` double DEFAULT NULL,
  `j_params` json NOT NULL,
  `j_index_stats` json DEFAULT NULL,
  `s_status` varchar(16) NOT NULL DEFAULT 'running',
  `s_error` text,
  `s_variant` varchar(8) NOT NULL DEFAULT 'raw',
  `s_feed` varchar(16) DEFAULT NULL,
  `s_osrm` varchar(12) DEFAULT NULL,
  `j_osrm` json DEFAULT NULL,
  `i_stops` int NOT NULL DEFAULT '0',
  `i_spikes` bigint NOT NULL DEFAULT '0',
  `i_medians` bigint NOT NULL DEFAULT '0',
  `i_snapped` bigint NOT NULL DEFAULT '0',
  `i_gaps` int NOT NULL DEFAULT '0',
  `i_moving_gaps` int NOT NULL DEFAULT '0',
  `i_gaps_routed` int NOT NULL DEFAULT '0',
  `i_inferred` int NOT NULL DEFAULT '0',
  `b_published` tinyint(1) NOT NULL DEFAULT '0',
  `dt_summarised` datetime DEFAULT NULL,
  PRIMARY KEY (`i_run_id`),
  KEY `idx_run_started` (`dt_started`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- geofencing.geo_event
CREATE TABLE IF NOT EXISTS `geo_event` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `i_run_id` int NOT NULL,
  `i_trip_no` bigint DEFAULT NULL,
  `s_asset_id` varchar(50) DEFAULT NULL,
  `i_fence_id` int NOT NULL,
  `i_site_id` int NOT NULL,
  `s_site_name` varchar(255) NOT NULL,
  `s_event` varchar(8) NOT NULL,
  `dt_event` datetime NOT NULL,
  `i_gap_seconds` int DEFAULT NULL,
  `d_lat` decimal(10,8) NOT NULL,
  `d_long` decimal(11,8) NOT NULL,
  `i_speed` smallint DEFAULT NULL,
  `s_confirmed_by` varchar(16) NOT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_ev_run` (`i_run_id`),
  KEY `idx_ev_trip` (`i_run_id`,`i_trip_no`,`dt_event`),
  KEY `idx_ev_fence` (`i_fence_id`,`dt_event`),
  KEY `idx_ev_asset` (`s_asset_id`,`dt_event`),
  KEY `idx_ev_site` (`i_run_id`,`i_site_id`,`dt_event`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- geofencing.geo_visit
CREATE TABLE IF NOT EXISTS `geo_visit` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `i_run_id` int NOT NULL,
  `i_trip_no` bigint DEFAULT NULL,
  `s_asset_id` varchar(50) DEFAULT NULL,
  `i_fence_id` int NOT NULL,
  `i_site_id` int NOT NULL,
  `s_site_name` varchar(255) NOT NULL,
  `s_type` varchar(40) DEFAULT NULL,
  `s_category` varchar(16) NOT NULL DEFAULT 'normal',
  `dt_enter` datetime NOT NULL,
  `dt_exit` datetime DEFAULT NULL,
  `b_open` tinyint(1) NOT NULL DEFAULT '0',
  `i_dwell_seconds` int DEFAULT NULL,
  `i_pings` int NOT NULL DEFAULT '0',
  `i_max_speed` smallint DEFAULT NULL,
  `d_distance_m` double DEFAULT NULL,
  `i_enter_gap_seconds` int DEFAULT NULL,
  `i_exit_gap_seconds` int DEFAULT NULL,
  `b_primary` tinyint(1) NOT NULL DEFAULT '1',
  `b_entry_observed` tinyint(1) NOT NULL DEFAULT '1',
  `s_confirmed_by` varchar(16) DEFAULT NULL,
  `s_scale` varchar(10) DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_visit_run` (`i_run_id`),
  KEY `idx_visit_trip` (`i_run_id`,`i_trip_no`),
  KEY `idx_visit_fence` (`i_fence_id`,`dt_enter`),
  KEY `idx_visit_asset` (`s_asset_id`,`dt_enter`),
  KEY `idx_visit_open` (`i_run_id`,`b_open`),
  KEY `idx_visit_primary` (`i_run_id`,`b_primary`,`dt_enter`),
  KEY `idx_visit_site` (`i_run_id`,`i_site_id`,`dt_enter`),
  KEY `idx_visit_time` (`i_run_id`,`dt_enter`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- geofencing.geo_violation
CREATE TABLE IF NOT EXISTS `geo_violation` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `i_run_id` int NOT NULL,
  `i_trip_no` bigint DEFAULT NULL,
  `s_asset_id` varchar(50) DEFAULT NULL,
  `i_fence_id` int NOT NULL,
  `i_site_id` int NOT NULL,
  `s_site_name` varchar(255) NOT NULL,
  `s_kind` varchar(24) NOT NULL,
  `dt_event` datetime NOT NULL,
  `d_lat` decimal(10,8) NOT NULL,
  `d_long` decimal(11,8) NOT NULL,
  `i_observed` int DEFAULT NULL,
  `i_limit` int DEFAULT NULL,
  `s_detail` varchar(255) DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_vio_run` (`i_run_id`,`s_kind`),
  KEY `idx_vio_trip` (`i_run_id`,`i_trip_no`),
  KEY `idx_vio_fence` (`i_fence_id`,`dt_event`),
  KEY `idx_vio_time` (`i_run_id`,`dt_event`),
  KEY `idx_vio_site` (`i_run_id`,`i_site_id`,`dt_event`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- geofencing.geo_trip_summary
CREATE TABLE IF NOT EXISTS `geo_trip_summary` (
  `i_run_id` int NOT NULL,
  `i_trip_no` bigint NOT NULL,
  `s_asset_id` varchar(50) DEFAULT NULL,
  `dt_first_ping` datetime DEFAULT NULL,
  `dt_last_ping` datetime DEFAULT NULL,
  `i_pings_read` int DEFAULT '0',
  `i_pings_used` int DEFAULT '0',
  `i_pings_dropped` int DEFAULT '0',
  `i_pings_inside` int DEFAULT '0',
  `i_visits` int DEFAULT '0',
  `i_distinct_sites` int DEFAULT '0',
  `i_violations` int DEFAULT '0',
  `i_inside_seconds` int DEFAULT '0',
  `i_max_gap_seconds` int DEFAULT NULL,
  `d_coverage_pct` double DEFAULT NULL,
  `s_quality` varchar(16) DEFAULT NULL,
  `i_stops` int NOT NULL DEFAULT '0',
  `i_spikes` int NOT NULL DEFAULT '0',
  `i_medians` int NOT NULL DEFAULT '0',
  `i_snapped` int NOT NULL DEFAULT '0',
  `i_moving_gaps` int NOT NULL DEFAULT '0',
  `i_moving_gap_s` int NOT NULL DEFAULT '0',
  `i_inferred` int NOT NULL DEFAULT '0',
  `s_quality_reason` varchar(96) DEFAULT NULL,
  `s_osrm` varchar(12) DEFAULT NULL,
  `d_distance_km` double DEFAULT NULL,
  `d_gap_distance_km` double DEFAULT NULL,
  `i_facility_visits` int NOT NULL DEFAULT '0',
  `i_facility_dwell_s` int NOT NULL DEFAULT '0',
  `i_places` int NOT NULL DEFAULT '0',
  `i_first_site_id` int DEFAULT NULL,
  `s_first_site` varchar(255) DEFAULT NULL,
  `dt_first_enter` datetime DEFAULT NULL,
  `dt_first_exit` datetime DEFAULT NULL,
  `i_last_site_id` int DEFAULT NULL,
  `s_last_site` varchar(255) DEFAULT NULL,
  `dt_last_enter` datetime DEFAULT NULL,
  `dt_last_exit` datetime DEFAULT NULL,
  `i_transit_s` int DEFAULT NULL,
  PRIMARY KEY (`i_run_id`,`i_trip_no`),
  KEY `idx_ts_asset` (`s_asset_id`),
  KEY `idx_ts_first` (`i_run_id`,`i_first_site_id`),
  KEY `idx_ts_last` (`i_run_id`,`i_last_site_id`),
  KEY `idx_ts_time` (`i_run_id`,`dt_first_ping`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- geofencing.geo_ping_reject
CREATE TABLE IF NOT EXISTS `geo_ping_reject` (
  `i_run_id` int NOT NULL,
  `i_trip_no` bigint NOT NULL,
  `s_reason` varchar(32) NOT NULL,
  `i_count` int NOT NULL,
  PRIMARY KEY (`i_run_id`,`i_trip_no`,`s_reason`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- geofencing.geo_asset_state
CREATE TABLE IF NOT EXISTS `geo_asset_state` (
  `s_asset_id` varchar(50) NOT NULL,
  `i_fence_id` int NOT NULL,
  `s_state` varchar(8) NOT NULL,
  `dt_since` datetime NOT NULL,
  `dt_last_ping` datetime NOT NULL,
  `i_run_id` int DEFAULT NULL,
  PRIMARY KEY (`s_asset_id`,`i_fence_id`),
  KEY `idx_state_fence` (`i_fence_id`,`s_state`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- geofencing.geo_fit_trail
CREATE TABLE IF NOT EXISTS `geo_fit_trail` (
  `i_run_id` int NOT NULL,
  `i_trip_no` bigint NOT NULL,
  `i_pings` int NOT NULL,
  `s_codec` varchar(16) NOT NULL,
  `m_data` mediumblob NOT NULL,
  PRIMARY KEY (`i_run_id`,`i_trip_no`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- geofencing.geo_trip_day
CREATE TABLE IF NOT EXISTS `geo_trip_day` (
  `i_run_id` int NOT NULL,
  `i_trip_no` bigint NOT NULL,
  `d_day` date NOT NULL,
  `s_asset_id` varchar(50) DEFAULT NULL,
  `i_pings` int NOT NULL DEFAULT '0',
  `i_rejected` int NOT NULL DEFAULT '0',
  `i_spikes` int NOT NULL DEFAULT '0',
  `i_medians` int NOT NULL DEFAULT '0',
  `i_snapped` int NOT NULL DEFAULT '0',
  PRIMARY KEY (`i_run_id`,`i_trip_no`,`d_day`),
  KEY `idx_td_day` (`i_run_id`,`d_day`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- geofencing.geo_stop
CREATE TABLE IF NOT EXISTS `geo_stop` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `i_run_id` int NOT NULL,
  `i_trip_no` bigint NOT NULL,
  `s_asset_id` varchar(50) DEFAULT NULL,
  `i_seq` int NOT NULL,
  `dt_start` datetime NOT NULL,
  `dt_end` datetime NOT NULL,
  `i_duration_s` int NOT NULL,
  `i_pings` int NOT NULL,
  `i_spikes` int NOT NULL DEFAULT '0',
  `i_max_gap_s` int DEFAULT NULL,
  `d_lat` decimal(10,8) NOT NULL,
  `d_long` decimal(11,8) NOT NULL,
  `d_p90_spread_m` float DEFAULT NULL,
  `i_fence_id` int DEFAULT NULL,
  `i_site_id` int DEFAULT NULL,
  `s_site_name` varchar(255) DEFAULT NULL,
  `s_scale` varchar(10) DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_stop_trip` (`i_run_id`,`i_trip_no`,`dt_start`),
  KEY `idx_stop_site` (`i_run_id`,`i_site_id`),
  KEY `idx_stop_time` (`i_run_id`,`dt_start`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- geofencing.geo_gap
CREATE TABLE IF NOT EXISTS `geo_gap` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `i_run_id` int NOT NULL,
  `i_trip_no` bigint NOT NULL,
  `s_asset_id` varchar(50) DEFAULT NULL,
  `dt_from` datetime NOT NULL,
  `dt_to` datetime NOT NULL,
  `i_gap_s` int NOT NULL,
  `d_from_lat` decimal(10,8) NOT NULL,
  `d_from_long` decimal(11,8) NOT NULL,
  `d_to_lat` decimal(10,8) NOT NULL,
  `d_to_long` decimal(11,8) NOT NULL,
  `d_straight_m` float NOT NULL,
  `s_kind` varchar(10) NOT NULL,
  `s_route` varchar(12) NOT NULL,
  `d_route_m` float DEFAULT NULL,
  `d_route_s` float DEFAULT NULL,
  `i_unexplained_s` int DEFAULT NULL,
  `s_geometry` mediumtext,
  PRIMARY KEY (`id`),
  KEY `idx_gap_trip` (`i_run_id`,`i_trip_no`,`dt_from`),
  KEY `idx_gap_time` (`i_run_id`,`dt_from`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- geofencing.geo_inferred_visit
CREATE TABLE IF NOT EXISTS `geo_inferred_visit` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `i_run_id` int NOT NULL,
  `i_trip_no` bigint NOT NULL,
  `s_asset_id` varchar(50) DEFAULT NULL,
  `dt_gap_from` datetime NOT NULL,
  `dt_gap_to` datetime NOT NULL,
  `i_fence_id` int NOT NULL,
  `i_site_id` int NOT NULL,
  `s_site_name` varchar(255) NOT NULL,
  `s_category` varchar(16) DEFAULT NULL,
  `s_scale` varchar(10) DEFAULT NULL,
  `s_kind` varchar(10) NOT NULL,
  `dt_est_enter` datetime DEFAULT NULL,
  `dt_est_exit` datetime DEFAULT NULL,
  `d_inside_m` float DEFAULT NULL,
  `d_min_dist_m` float DEFAULT NULL,
  `s_confidence` varchar(8) NOT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_iv_trip` (`i_run_id`,`i_trip_no`),
  KEY `idx_iv_site` (`i_run_id`,`i_site_id`,`dt_gap_from`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- geofencing.geo_fence_stats
CREATE TABLE IF NOT EXISTS `geo_fence_stats` (
  `i_run_id` int NOT NULL,
  `i_fence_id` int NOT NULL,
  `i_site_id` int NOT NULL,
  `s_site_name` varchar(255) NOT NULL,
  `s_type` varchar(40) DEFAULT NULL,
  `s_category` varchar(16) DEFAULT NULL,
  `s_scale` varchar(10) DEFAULT NULL,
  `i_visits` int NOT NULL DEFAULT '0',
  `i_primary_visits` int NOT NULL DEFAULT '0',
  `i_vehicles` int NOT NULL DEFAULT '0',
  `i_trips` int NOT NULL DEFAULT '0',
  `i_transporters` int NOT NULL DEFAULT '0',
  `i_open_visits` int NOT NULL DEFAULT '0',
  `i_unobserved_entries` int NOT NULL DEFAULT '0',
  `i_dwell_total_s` bigint NOT NULL DEFAULT '0',
  `i_dwell_p50_s` int DEFAULT NULL,
  `i_dwell_p90_s` int DEFAULT NULL,
  `i_dwell_max_s` int DEFAULT NULL,
  `i_violations` int NOT NULL DEFAULT '0',
  `i_inferred` int NOT NULL DEFAULT '0',
  `i_stops` int NOT NULL DEFAULT '0',
  `dt_first` datetime DEFAULT NULL,
  `dt_last` datetime DEFAULT NULL,
  PRIMARY KEY (`i_run_id`,`i_fence_id`),
  KEY `idx_fs_site` (`i_run_id`,`i_site_id`),
  KEY `idx_fs_visits` (`i_run_id`,`i_visits`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- geofencing.geo_fence_day
CREATE TABLE IF NOT EXISTS `geo_fence_day` (
  `i_run_id` int NOT NULL,
  `i_fence_id` int NOT NULL,
  `d_day` date NOT NULL,
  `i_site_id` int NOT NULL,
  `i_entries` int NOT NULL DEFAULT '0',
  `i_exits` int NOT NULL DEFAULT '0',
  `i_vehicles` int NOT NULL DEFAULT '0',
  `i_dwell_s` bigint NOT NULL DEFAULT '0',
  `i_dwell_p50_s` int DEFAULT NULL,
  PRIMARY KEY (`i_run_id`,`i_fence_id`,`d_day`),
  KEY `idx_fd_day` (`i_run_id`,`d_day`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- geofencing.geo_day_summary
CREATE TABLE IF NOT EXISTS `geo_day_summary` (
  `i_run_id` int NOT NULL,
  `d_day` date NOT NULL,
  `i_vehicles` int NOT NULL DEFAULT '0',
  `i_trips` int NOT NULL DEFAULT '0',
  `i_pings` int NOT NULL DEFAULT '0',
  `i_pings_rejected` int NOT NULL DEFAULT '0',
  `i_spikes` int NOT NULL DEFAULT '0',
  `i_entries` int NOT NULL DEFAULT '0',
  `i_exits` int NOT NULL DEFAULT '0',
  `i_facility_visits` int NOT NULL DEFAULT '0',
  `i_sites_visited` int NOT NULL DEFAULT '0',
  `i_facility_dwell_s` bigint NOT NULL DEFAULT '0',
  `i_restricted` int NOT NULL DEFAULT '0',
  `i_overspeed` int NOT NULL DEFAULT '0',
  `i_stops` int NOT NULL DEFAULT '0',
  `i_stops_outside` int NOT NULL DEFAULT '0',
  `i_stop_outside_s` bigint NOT NULL DEFAULT '0',
  `i_moving_gaps` int NOT NULL DEFAULT '0',
  `i_inferred` int NOT NULL DEFAULT '0',
  `j_hourly` json DEFAULT NULL,
  PRIMARY KEY (`i_run_id`,`d_day`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- geofencing.geo_pvisit
CREATE TABLE IF NOT EXISTS `geo_pvisit` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `i_run_id` int NOT NULL,
  `s_asset_id` varchar(50) DEFAULT NULL,
  `i_fence_id` int NOT NULL,
  `i_site_id` int NOT NULL,
  `s_site_name` varchar(255) NOT NULL,
  `s_type` varchar(40) DEFAULT NULL,
  `s_category` varchar(16) DEFAULT NULL,
  `s_scale` varchar(10) DEFAULT NULL,
  `dt_enter` datetime NOT NULL,
  `dt_exit` datetime DEFAULT NULL,
  `b_open` tinyint(1) NOT NULL,
  `b_entry_observed` tinyint(1) NOT NULL,
  `i_dwell_seconds` int NOT NULL,
  `b_primary` tinyint(1) NOT NULL,
  `i_trips` smallint NOT NULL,
  `i_trip_no` bigint NOT NULL,
  `s_trips` varchar(512) DEFAULT NULL,
  `s_trans_name` varchar(255) DEFAULT NULL,
  `s_driver_name` varchar(255) DEFAULT NULL,
  `i_enter_gap_seconds` int DEFAULT NULL,
  `i_exit_gap_seconds` int DEFAULT NULL,
  `s_confirmed_by` varchar(16) DEFAULT NULL,
  `i_max_speed` smallint DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_pv_fence` (`i_run_id`,`i_fence_id`,`dt_enter`),
  KEY `idx_pv_site` (`i_run_id`,`i_site_id`,`dt_enter`),
  KEY `idx_pv_time` (`i_run_id`,`dt_enter`),
  KEY `idx_pv_asset` (`i_run_id`,`s_asset_id`,`dt_enter`),
  KEY `idx_pv_trans` (`i_run_id`,`s_trans_name`),
  KEY `idx_pv_driver` (`i_run_id`,`s_driver_name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- geofencing.geo_palert
CREATE TABLE IF NOT EXISTS `geo_palert` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `i_run_id` int NOT NULL,
  `s_asset_id` varchar(50) DEFAULT NULL,
  `i_fence_id` int NOT NULL,
  `i_site_id` int NOT NULL,
  `s_site_name` varchar(255) NOT NULL,
  `s_kind` varchar(24) NOT NULL,
  `dt_event` datetime NOT NULL,
  `d_lat` decimal(10,8) NOT NULL,
  `d_long` decimal(11,8) NOT NULL,
  `i_observed` int DEFAULT NULL,
  `i_limit` int DEFAULT NULL,
  `s_detail` varchar(255) DEFAULT NULL,
  `i_trips` smallint NOT NULL,
  `i_trip_no` bigint NOT NULL,
  `s_trips` varchar(512) DEFAULT NULL,
  `s_trans_name` varchar(255) DEFAULT NULL,
  `s_driver_name` varchar(255) DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_pa_time` (`i_run_id`,`dt_event`),
  KEY `idx_pa_site` (`i_run_id`,`i_site_id`,`dt_event`),
  KEY `idx_pa_asset` (`i_run_id`,`s_asset_id`,`dt_event`),
  KEY `idx_pa_trans` (`i_run_id`,`s_trans_name`),
  KEY `idx_pa_driver` (`i_run_id`,`s_driver_name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- geofencing.geo_pstop
CREATE TABLE IF NOT EXISTS `geo_pstop` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `i_run_id` int NOT NULL,
  `s_asset_id` varchar(50) DEFAULT NULL,
  `dt_start` datetime NOT NULL,
  `dt_end` datetime NOT NULL,
  `i_duration_s` int NOT NULL,
  `i_pings` int NOT NULL,
  `d_lat` decimal(10,8) NOT NULL,
  `d_long` decimal(11,8) NOT NULL,
  `d_p90_spread_m` float DEFAULT NULL,
  `i_fence_id` int DEFAULT NULL,
  `i_site_id` int DEFAULT NULL,
  `s_site_name` varchar(255) DEFAULT NULL,
  `s_scale` varchar(10) DEFAULT NULL,
  `i_trips` smallint NOT NULL,
  `i_trip_no` bigint NOT NULL,
  `s_trips` varchar(512) DEFAULT NULL,
  `s_trans_name` varchar(255) DEFAULT NULL,
  `s_origin` varchar(255) DEFAULT NULL,
  `s_destination` varchar(255) DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_ps_time` (`i_run_id`,`dt_start`),
  KEY `idx_ps_site` (`i_run_id`,`i_site_id`),
  KEY `idx_ps_asset` (`i_run_id`,`s_asset_id`,`dt_start`),
  KEY `idx_ps_outside` (`i_run_id`,`i_fence_id`,`i_duration_s`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- geofencing.geo_trip_share
CREATE TABLE IF NOT EXISTS `geo_trip_share` (
  `i_run_id` int NOT NULL,
  `i_trip_no` bigint NOT NULL,
  `i_facility_visits` int NOT NULL DEFAULT '0',
  `i_facility_dwell_s` int NOT NULL DEFAULT '0',
  `i_alerts` int NOT NULL DEFAULT '0',
  `i_overspeed` int NOT NULL DEFAULT '0',
  `i_restricted` int NOT NULL DEFAULT '0',
  `i_inferred` int NOT NULL DEFAULT '0',
  `d_distance_km` double DEFAULT NULL,
  `d_own_distance_km` double DEFAULT NULL,
  `i_siblings` smallint NOT NULL DEFAULT '0',
  `s_sibling_trips` varchar(512) DEFAULT NULL,
  PRIMARY KEY (`i_run_id`,`i_trip_no`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- geofencing.geo_toll_plaza
CREATE TABLE IF NOT EXISTS `geo_toll_plaza` (
  `i_plaza_id` int NOT NULL AUTO_INCREMENT,
  `s_netc_code` varchar(16) DEFAULT NULL,
  `s_name` varchar(160) NOT NULL,
  `s_state` varchar(64) NOT NULL,
  `s_state_listed` varchar(64) DEFAULT NULL,
  `s_district` varchar(96) DEFAULT NULL,
  `s_section` varchar(1024) DEFAULT NULL,
  `s_nh` varchar(64) DEFAULT NULL,
  `s_piu` varchar(96) DEFAULT NULL,
  `s_regional_office` varchar(96) DEFAULT NULL,
  `d_lat` decimal(10,7) DEFAULT NULL,
  `d_long` decimal(10,7) DEFAULT NULL,
  `s_source` varchar(32) NOT NULL,
  `d_as_of` date DEFAULT NULL,
  PRIMARY KEY (`i_plaza_id`),
  KEY `idx_toll_state` (`s_state`),
  KEY `idx_toll_code` (`s_netc_code`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- geofencing.geo_trip_phase
CREATE TABLE IF NOT EXISTS `geo_trip_phase` (
  `i_run_id` int NOT NULL,
  `i_trip_no` bigint NOT NULL,
  `s_asset_id` varchar(50) DEFAULT NULL,
  `dt_start` datetime DEFAULT NULL,
  `dt_end` datetime DEFAULT NULL,
  `i_span_s` int DEFAULT NULL,
  `s_shape` varchar(10) NOT NULL,
  `i_places` smallint NOT NULL DEFAULT '0',
  `i_loading_site_id` int DEFAULT NULL,
  `s_loading_site` varchar(255) DEFAULT NULL,
  `dt_loading_in` datetime DEFAULT NULL,
  `dt_loading_out` datetime DEFAULT NULL,
  `i_loading_s` int DEFAULT NULL,
  `b_loading_in_seen` tinyint(1) DEFAULT NULL,
  `i_unloading_site_id` int DEFAULT NULL,
  `s_unloading_site` varchar(255) DEFAULT NULL,
  `dt_unloading_in` datetime DEFAULT NULL,
  `dt_unloading_out` datetime DEFAULT NULL,
  `i_unloading_s` int DEFAULT NULL,
  `b_unloading_open` tinyint(1) DEFAULT NULL,
  `i_transit_s` int DEFAULT NULL,
  `i_transit_moving_s` int DEFAULT NULL,
  `i_transit_stop_s` int DEFAULT NULL,
  `i_transit_halt_s` int DEFAULT NULL,
  `i_transit_silent_s` int DEFAULT NULL,
  `i_transit_stops` smallint DEFAULT NULL,
  `i_transit_halts` smallint DEFAULT NULL,
  `i_before_s` int DEFAULT NULL,
  `i_after_s` int DEFAULT NULL,
  `d_km` double DEFAULT NULL,
  `d_transit_km` double DEFAULT NULL,
  `d_transit_silent_km` double DEFAULT NULL,
  `d_transit_kmph` float DEFAULT NULL,
  `j_bar` text,
  PRIMARY KEY (`i_run_id`,`i_trip_no`),
  KEY `idx_tp_time` (`i_run_id`,`dt_start`),
  KEY `idx_tp_asset` (`i_run_id`,`s_asset_id`,`dt_start`),
  KEY `idx_tp_lane` (`i_run_id`,`i_loading_site_id`,`i_unloading_site_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- geofencing.geo_state
CREATE TABLE IF NOT EXISTS `geo_state` (
  `s_key` varchar(64) NOT NULL,
  `s_value` varchar(255) DEFAULT NULL,
  `dt_updated` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`s_key`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- geofencing.geo_job
CREATE TABLE IF NOT EXISTS `geo_job` (
  `i_job_id` int NOT NULL AUTO_INCREMENT,
  `s_kind` varchar(16) NOT NULL,
  `s_trigger` varchar(16) NOT NULL,
  `i_run_id` int DEFAULT NULL,
  `dt_started` datetime(3) NOT NULL,
  `dt_finished` datetime(3) DEFAULT NULL,
  `s_status` varchar(12) NOT NULL,
  `i_new_pings` int DEFAULT NULL,
  `i_dirty_trips` int DEFAULT NULL,
  `i_affected_trips` int DEFAULT NULL,
  `i_days` int DEFAULT NULL,
  `i_fences` int DEFAULT NULL,
  `d_seconds` double DEFAULT NULL,
  `j_stats` json DEFAULT NULL,
  `s_error` text,
  PRIMARY KEY (`i_job_id`),
  KEY `idx_job_time` (`dt_started`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- geofencing.geo_upload
CREATE TABLE IF NOT EXISTS `geo_upload` (
  `i_upload_id` bigint NOT NULL AUTO_INCREMENT,
  `s_name` varchar(255) NOT NULL,
  `s_label` varchar(255) DEFAULT NULL,
  `s_sha256` char(64) NOT NULL,
  `i_bytes` bigint NOT NULL DEFAULT '0',
  `s_asset_id` varchar(50) DEFAULT NULL,
  `i_source_trip` bigint DEFAULT NULL,
  `s_status` varchar(16) NOT NULL DEFAULT 'parsing',
  `s_error` varchar(500) DEFAULT NULL,
  `i_rows_read` int NOT NULL DEFAULT '0',
  `i_rows_parsed` int NOT NULL DEFAULT '0',
  `dt_first_ping` datetime DEFAULT NULL,
  `dt_last_ping` datetime DEFAULT NULL,
  `i_pings_used` int NOT NULL DEFAULT '0',
  `i_visits` int NOT NULL DEFAULT '0',
  `i_places` int NOT NULL DEFAULT '0',
  `i_distinct_sites` int NOT NULL DEFAULT '0',
  `i_violations` int NOT NULL DEFAULT '0',
  `d_distance_km` decimal(10,3) DEFAULT NULL,
  `s_quality` varchar(16) DEFAULT NULL,
  `d_seconds` decimal(10,3) DEFAULT NULL,
  `j_params` json DEFAULT NULL,
  `j_columns` json DEFAULT NULL,
  `dt_uploaded` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `dt_analysed` datetime DEFAULT NULL,
  PRIMARY KEY (`i_upload_id`),
  KEY `idx_up_sha` (`s_sha256`),
  KEY `idx_up_when` (`dt_uploaded`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- geofencing.geo_upload_ping
CREATE TABLE IF NOT EXISTS `geo_upload_ping` (
  `i_upload_id` bigint NOT NULL,
  `i_seq` int NOT NULL,
  `i_row` int NOT NULL,
  `dt_message` datetime DEFAULT NULL,
  `d_lat` decimal(10,7) DEFAULT NULL,
  `d_long` decimal(10,7) DEFAULT NULL,
  `i_speed` smallint DEFAULT NULL,
  PRIMARY KEY (`i_upload_id`,`i_seq`),
  KEY `idx_upp_ts` (`i_upload_id`,`dt_message`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- geofencing.geo_upload_artifact
CREATE TABLE IF NOT EXISTS `geo_upload_artifact` (
  `i_upload_id` bigint NOT NULL,
  `s_key` varchar(48) NOT NULL,
  `i_seq` int NOT NULL DEFAULT '0',
  `s_title` varchar(160) NOT NULL,
  `i_rows` int NOT NULL DEFAULT '0',
  `m_data` longblob NOT NULL,
  PRIMARY KEY (`i_upload_id`,`s_key`),
  KEY `idx_upa_seq` (`i_upload_id`,`i_seq`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- geofencing.geo_live_position
CREATE TABLE IF NOT EXISTS `geo_live_position` (
  `s_asset_id` varchar(50) NOT NULL,
  `dt_message` datetime NOT NULL,
  `d_lat` decimal(10,8) NOT NULL,
  `d_long` decimal(11,8) NOT NULL,
  `i_speed` smallint DEFAULT NULL,
  `i_trip_no` bigint DEFAULT NULL,
  `i_fence_id` int DEFAULT NULL,
  `i_site_id` int DEFAULT NULL,
  `s_site_name` varchar(255) DEFAULT NULL,
  `s_category` varchar(16) DEFAULT NULL,
  `i_inside_count` smallint NOT NULL DEFAULT '0',
  `dt_updated` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `s_scale` varchar(10) DEFAULT NULL,
  PRIMARY KEY (`s_asset_id`),
  KEY `idx_live_time` (`dt_message`),
  KEY `idx_live_fence` (`i_fence_id`),
  KEY `idx_live_scale` (`s_scale`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- geofencing.geo_live_event
CREATE TABLE IF NOT EXISTS `geo_live_event` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `dt_event` datetime NOT NULL,
  `dt_detected` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `s_asset_id` varchar(50) NOT NULL,
  `i_trip_no` bigint DEFAULT NULL,
  `i_fence_id` int NOT NULL,
  `i_site_id` int NOT NULL,
  `s_site_name` varchar(255) NOT NULL,
  `s_category` varchar(16) DEFAULT NULL,
  `s_scale` varchar(10) DEFAULT NULL,
  `s_event` varchar(16) NOT NULL,
  `s_severity` varchar(8) NOT NULL DEFAULT 'info',
  `i_gap_seconds` int DEFAULT NULL,
  `d_lat` decimal(10,8) NOT NULL,
  `d_long` decimal(11,8) NOT NULL,
  `i_speed` smallint DEFAULT NULL,
  `i_limit` smallint DEFAULT NULL,
  `s_detail` varchar(255) DEFAULT NULL,
  `s_confirmed_by` varchar(16) DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_le_time` (`id`),
  KEY `idx_le_asset` (`s_asset_id`,`dt_event`),
  KEY `idx_le_sev` (`s_severity`,`id`),
  KEY `idx_le_fence` (`i_fence_id`,`dt_event`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- geofencing.geo_live_cursor
CREATE TABLE IF NOT EXISTS `geo_live_cursor` (
  `i_id` tinyint NOT NULL DEFAULT '1',
  `i_last_ping_id` bigint NOT NULL DEFAULT '0',
  `dt_last_message` datetime DEFAULT NULL,
  `dt_updated` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `i_processed` bigint NOT NULL DEFAULT '0',
  PRIMARY KEY (`i_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- smart_truck.geofences
CREATE TABLE IF NOT EXISTS `geofences` (
  `i_fence_id` int NOT NULL AUTO_INCREMENT,
  `s_key` varchar(255) NOT NULL,
  `s_name` varchar(255) NOT NULL,
  `s_role` varchar(12) NOT NULL,
  `d_lat` decimal(10,8) NOT NULL,
  `d_long` decimal(11,8) NOT NULL,
  `i_radius_m` int NOT NULL DEFAULT '3000',
  `i_trips_seen` int DEFAULT '0',
  `i_spread_m` int DEFAULT NULL,
  `b_manual` tinyint(1) NOT NULL DEFAULT '0',
  `b_active` tinyint(1) NOT NULL DEFAULT '1',
  `s_source` varchar(20) NOT NULL DEFAULT 'anchor',
  `dt_created` timestamp NULL DEFAULT CURRENT_TIMESTAMP,
  `dt_modified` timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`i_fence_id`),
  UNIQUE KEY `uq_geofence_key_role` (`s_key`,`s_role`),
  KEY `idx_geofence_active` (`b_active`,`s_role`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- smart_truck.tta_trip_geofence_events
CREATE TABLE IF NOT EXISTS `tta_trip_geofence_events` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `i_trip_no` bigint NOT NULL,
  `i_fence_id` int NOT NULL,
  `s_fence_key` varchar(255) NOT NULL,
  `s_role` varchar(12) NOT NULL,
  `s_event` varchar(8) NOT NULL,
  `dt_event` datetime NOT NULL,
  `i_gap_min` int DEFAULT NULL,
  `d_lat` decimal(10,8) NOT NULL,
  `d_long` decimal(11,8) NOT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_gfe_trip` (`i_trip_no`),
  KEY `idx_gfe_fence` (`i_fence_id`,`s_event`),
  KEY `idx_gfe_time` (`dt_event`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- smart_truck.gps_facility_anchors
CREATE TABLE IF NOT EXISTS `gps_facility_anchors` (
  `s_node_name` varchar(255) NOT NULL,
  `s_role` varchar(12) NOT NULL,
  `d_lat` decimal(10,8) NOT NULL,
  `d_long` decimal(11,8) NOT NULL,
  `i_trips` int DEFAULT '0',
  `i_spread_m` int DEFAULT NULL,
  `dt_refreshed` timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`s_node_name`,`s_role`),
  KEY `idx_anchor_geo` (`d_lat`,`d_long`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
