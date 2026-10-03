-- nx_route: plans, trip routes and deviations (Geo-Fencing's routing
-- tables, as its live schema had them) and the weather cache.


-- geofencing.geo_route_plan
CREATE TABLE IF NOT EXISTS `geo_route_plan` (
  `i_plan_id` int NOT NULL AUTO_INCREMENT,
  `s_mode` varchar(8) NOT NULL,
  `s_from_key` varchar(40) NOT NULL,
  `s_to_key` varchar(40) NOT NULL,
  `i_from_site` int DEFAULT NULL,
  `i_to_site` int DEFAULT NULL,
  `d_from_lat` decimal(10,7) DEFAULT NULL,
  `d_from_long` decimal(10,7) DEFAULT NULL,
  `d_to_lat` decimal(10,7) DEFAULT NULL,
  `d_to_long` decimal(10,7) DEFAULT NULL,
  `d_distance_m` double DEFAULT NULL,
  `d_duration_s` double DEFAULT NULL,
  `d_transit_s` double DEFAULT NULL,
  `s_polyline` mediumtext,
  `i_points` int DEFAULT NULL,
  `i_ref_trip` bigint DEFAULT NULL,
  `i_sample` int DEFAULT NULL,
  `s_version` varchar(64) DEFAULT NULL,
  `j_meta` json DEFAULT NULL,
  `dt_built` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`i_plan_id`),
  UNIQUE KEY `uq_route_plan` (`s_mode`,`s_from_key`,`s_to_key`),
  KEY `idx_plan_lane` (`i_from_site`,`i_to_site`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- geofencing.geo_trip_route
CREATE TABLE IF NOT EXISTS `geo_trip_route` (
  `i_run_id` int NOT NULL,
  `i_trip_no` bigint NOT NULL,
  `s_asset_id` varchar(50) DEFAULT NULL,
  `s_mode` varchar(8) NOT NULL,
  `s_status` varchar(16) NOT NULL,
  `i_plan_id` int DEFAULT NULL,
  `i_from_site` int DEFAULT NULL,
  `s_from_site` varchar(255) DEFAULT NULL,
  `i_to_site` int DEFAULT NULL,
  `s_to_site` varchar(255) DEFAULT NULL,
  `s_trans_name` varchar(255) DEFAULT NULL,
  `dt_transit_from` datetime DEFAULT NULL,
  `dt_transit_to` datetime DEFAULT NULL,
  `d_planned_km` double DEFAULT NULL,
  `i_planned_drive_s` int DEFAULT NULL,
  `i_planned_transit_s` int DEFAULT NULL,
  `d_actual_km` double DEFAULT NULL,
  `i_actual_drive_s` int DEFAULT NULL,
  `i_actual_transit_s` int DEFAULT NULL,
  `i_stop_s` int DEFAULT NULL,
  `i_halt_s` int DEFAULT NULL,
  `d_extra_km` double DEFAULT NULL,
  `d_extra_pct` float DEFAULT NULL,
  `i_extra_s` int DEFAULT NULL,
  `d_offroute_km` double DEFAULT NULL,
  `i_offroute_s` int DEFAULT NULL,
  `i_deviations` smallint NOT NULL DEFAULT '0',
  `i_detours` smallint NOT NULL DEFAULT '0',
  `i_offroute_stops` smallint NOT NULL DEFAULT '0',
  `i_reroutes` smallint NOT NULL DEFAULT '0',
  `d_max_offset_m` float DEFAULT NULL,
  `d_adherence_pct` float DEFAULT NULL,
  `d_cost_plan` double DEFAULT NULL,
  `d_cost_actual` double DEFAULT NULL,
  `d_variance` double DEFAULT NULL,
  `d_variance_km` double DEFAULT NULL,
  `d_variance_time` double DEFAULT NULL,
  `d_detention_h` float DEFAULT NULL,
  `d_detention_cost` double DEFAULT NULL,
  `d_revenue` double DEFAULT NULL,
  `d_margin` double DEFAULT NULL,
  `s_verdict` varchar(8) DEFAULT NULL,
  `i_ref_trip` bigint DEFAULT NULL,
  PRIMARY KEY (`i_run_id`,`i_trip_no`),
  KEY `idx_tr_time` (`i_run_id`,`dt_transit_from`),
  KEY `idx_tr_lane` (`i_run_id`,`i_from_site`,`i_to_site`),
  KEY `idx_tr_asset` (`i_run_id`,`s_asset_id`),
  KEY `idx_tr_verdict` (`i_run_id`,`s_verdict`,`d_variance`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- geofencing.geo_route_deviation
CREATE TABLE IF NOT EXISTS `geo_route_deviation` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `i_run_id` int NOT NULL,
  `i_trip_no` bigint NOT NULL,
  `i_seq` smallint NOT NULL,
  `s_kind` varchar(16) NOT NULL,
  `dt_leave` datetime NOT NULL,
  `dt_back` datetime DEFAULT NULL,
  `d_leave_lat` decimal(10,7) NOT NULL,
  `d_leave_long` decimal(10,7) NOT NULL,
  `d_back_lat` decimal(10,7) DEFAULT NULL,
  `d_back_long` decimal(10,7) DEFAULT NULL,
  `d_chain_leave_km` double DEFAULT NULL,
  `d_chain_back_km` double DEFAULT NULL,
  `d_actual_km` double DEFAULT NULL,
  `d_planned_km` double DEFAULT NULL,
  `d_extra_km` double DEFAULT NULL,
  `i_duration_s` int DEFAULT NULL,
  `i_stop_s` int DEFAULT NULL,
  `d_max_offset_m` float DEFAULT NULL,
  `b_silent` tinyint(1) NOT NULL DEFAULT '0',
  `b_reroute` tinyint(1) NOT NULL DEFAULT '0',
  `d_new_route_km` double DEFAULT NULL,
  `i_plan_index` smallint NOT NULL DEFAULT '0',
  PRIMARY KEY (`id`),
  KEY `idx_rd_trip` (`i_run_id`,`i_trip_no`,`i_seq`),
  KEY `idx_rd_kind` (`i_run_id`,`s_kind`,`dt_leave`),
  KEY `idx_rd_time` (`i_run_id`,`dt_leave`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- smart_truck.tta_weather_cache
CREATE TABLE IF NOT EXISTS `tta_weather_cache` (
  `cache_key` varchar(48) NOT NULL,
  `lat` double NOT NULL,
  `lng` double NOT NULL,
  `date_str` char(10) NOT NULL,
  `payload_json` mediumtext,
  `fetched_at` datetime NOT NULL,
  PRIMARY KEY (`cache_key`),
  KEY `idx_twc_day` (`date_str`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
