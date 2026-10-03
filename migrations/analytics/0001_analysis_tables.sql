-- nx_analytics: every table Smart-Truck's analysis code writes -- the
-- historic trip store and its summaries (family A), the network, waypoint,
-- speed and hotspot aggregates, caches and settings -- exactly as Smart-Truck's
-- live schema had them. Foreign keys to the TTA feed tables are dropped:
-- those names are views over the fleet service now (R__aliases.sql).


-- smart_truck.drivers
CREATE TABLE IF NOT EXISTS `drivers` (
  `id` int NOT NULL AUTO_INCREMENT,
  `name` varchar(255) NOT NULL,
  `mobile1` varchar(20) DEFAULT NULL,
  `mobile2` varchar(20) DEFAULT NULL,
  `created_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_driver_name_mobile` (`name`,`mobile1`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- smart_truck.vehicles
CREATE TABLE IF NOT EXISTS `vehicles` (
  `id` int NOT NULL AUTO_INCREMENT,
  `asset_id` varchar(50) NOT NULL,
  `asset_type` varchar(100) DEFAULT NULL,
  `trailer_type` varchar(100) DEFAULT NULL,
  `created_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `asset_id` (`asset_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- smart_truck.locations
CREATE TABLE IF NOT EXISTS `locations` (
  `id` int NOT NULL AUTO_INCREMENT,
  `name` varchar(255) NOT NULL,
  `created_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `name` (`name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- smart_truck.customers
CREATE TABLE IF NOT EXISTS `customers` (
  `id` int NOT NULL AUTO_INCREMENT,
  `cne_name` varchar(255) DEFAULT NULL,
  `cne_id` int DEFAULT NULL,
  `cust_login_id` varchar(255) DEFAULT NULL,
  `created_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_customer_login` (`cust_login_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- smart_truck.trips
CREATE TABLE IF NOT EXISTS `trips` (
  `id` int NOT NULL AUTO_INCREMENT,
  `dispatch_entry_no` varchar(100) NOT NULL,
  `driver_id` int DEFAULT NULL,
  `vehicle_id` int DEFAULT NULL,
  `origin_id` int DEFAULT NULL,
  `destination_id` int DEFAULT NULL,
  `customer_id` int DEFAULT NULL,
  `trip_start` datetime DEFAULT NULL,
  `trip_end` datetime DEFAULT NULL,
  `trip_eta` datetime DEFAULT NULL,
  `ata_in` datetime DEFAULT NULL,
  `ata_out` datetime DEFAULT NULL,
  `trip_closed_at` datetime DEFAULT NULL,
  `dt_created` datetime DEFAULT NULL,
  `dt_updated` datetime DEFAULT NULL,
  `trip_km` decimal(10,2) DEFAULT NULL,
  `total_dist` decimal(10,2) DEFAULT NULL,
  `cover_dist` decimal(10,2) DEFAULT NULL,
  `trip_duration_minutes` decimal(12,2) DEFAULT NULL,
  `eta_met` tinyint(1) DEFAULT NULL,
  `eta_delay_minutes` decimal(12,2) DEFAULT NULL,
  `avg_speed_kmph` decimal(8,2) DEFAULT NULL,
  `eta_data_status` varchar(25) DEFAULT 'available',
  `trip_status` varchar(20) DEFAULT NULL,
  `is_active` varchar(5) DEFAULT NULL,
  `trip_close_remark` text,
  `material_desc` varchar(500) DEFAULT NULL,
  `invoice_no` varchar(100) DEFAULT NULL,
  `invoice_date` varchar(50) DEFAULT NULL,
  `ref_no` varchar(100) DEFAULT NULL,
  `ref_date` varchar(50) DEFAULT NULL,
  `entry_type` varchar(50) DEFAULT NULL,
  `own_market_type` varchar(50) DEFAULT NULL,
  `running_sts` varchar(50) DEFAULT NULL,
  `trip_seq` int DEFAULT NULL,
  `delay_by` decimal(10,2) DEFAULT NULL,
  `device_id` varchar(100) DEFAULT NULL,
  `device_type` varchar(50) DEFAULT NULL,
  `entity_id` int DEFAULT NULL,
  `cnr_id` int DEFAULT NULL,
  `created_by` varchar(100) DEFAULT NULL,
  `updated_by` varchar(100) DEFAULT NULL,
  `track_link` text,
  `data_string` text,
  `is_5am_default` tinyint(1) DEFAULT '0',
  `weather_conditions` varchar(100) DEFAULT NULL,
  `fuel_consumed` decimal(10,2) DEFAULT NULL,
  `load_weight_kg` decimal(10,2) DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `dispatch_entry_no` (`dispatch_entry_no`),
  KEY `destination_id` (`destination_id`),
  KEY `customer_id` (`customer_id`),
  KEY `idx_trips_driver_id` (`driver_id`),
  KEY `idx_trips_vehicle_id` (`vehicle_id`),
  KEY `idx_trips_origin_dest` (`origin_id`,`destination_id`),
  KEY `idx_trips_eta_met` (`eta_met`),
  KEY `idx_trips_trip_start` (`trip_start`),
  KEY `idx_trips_status` (`trip_status`),
  KEY `idx_trips_driver_start` (`driver_id`,`trip_start` DESC),
  KEY `idx_trips_eta_data_status` (`eta_data_status`),
  CONSTRAINT `trips_ibfk_1` FOREIGN KEY (`driver_id`) REFERENCES `drivers` (`id`) ON DELETE SET NULL,
  CONSTRAINT `trips_ibfk_2` FOREIGN KEY (`vehicle_id`) REFERENCES `vehicles` (`id`) ON DELETE SET NULL,
  CONSTRAINT `trips_ibfk_3` FOREIGN KEY (`origin_id`) REFERENCES `locations` (`id`) ON DELETE SET NULL,
  CONSTRAINT `trips_ibfk_4` FOREIGN KEY (`destination_id`) REFERENCES `locations` (`id`) ON DELETE SET NULL,
  CONSTRAINT `trips_ibfk_5` FOREIGN KEY (`customer_id`) REFERENCES `customers` (`id`) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- smart_truck.waypoints
CREATE TABLE IF NOT EXISTS `waypoints` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `vehicle_id` int DEFAULT NULL,
  `trip_id` int DEFAULT NULL,
  `latitude` decimal(10,7) DEFAULT NULL,
  `longitude` decimal(10,7) DEFAULT NULL,
  `speed_kmph` decimal(8,2) DEFAULT NULL,
  `status` varchar(100) DEFAULT NULL,
  `location_text` text,
  `distance_from_prev` decimal(10,2) DEFAULT NULL,
  `recorded_at` datetime NOT NULL,
  `created_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_waypoints_vehicle_time` (`vehicle_id`,`recorded_at` DESC),
  KEY `idx_waypoints_trip` (`trip_id`),
  KEY `idx_waypoints_recorded` (`recorded_at`),
  CONSTRAINT `waypoints_ibfk_1` FOREIGN KEY (`vehicle_id`) REFERENCES `vehicles` (`id`) ON DELETE SET NULL,
  CONSTRAINT `waypoints_ibfk_2` FOREIGN KEY (`trip_id`) REFERENCES `trips` (`id`) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- smart_truck.alerts
CREATE TABLE IF NOT EXISTS `alerts` (
  `id` int NOT NULL AUTO_INCREMENT,
  `alert_type` varchar(30) NOT NULL,
  `severity` varchar(10) NOT NULL,
  `trip_id` int DEFAULT NULL,
  `driver_id` int DEFAULT NULL,
  `vehicle_id` int DEFAULT NULL,
  `title` varchar(255) NOT NULL,
  `message` text,
  `metadata` json DEFAULT NULL,
  `is_read` tinyint(1) DEFAULT '0',
  `is_acknowledged` tinyint(1) DEFAULT '0',
  `created_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP,
  `acknowledged_at` datetime DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `trip_id` (`trip_id`),
  KEY `vehicle_id` (`vehicle_id`),
  KEY `idx_alerts_unread` (`is_acknowledged`,`severity`,`created_at` DESC),
  KEY `idx_alerts_driver` (`driver_id`,`created_at` DESC),
  CONSTRAINT `alerts_ibfk_1` FOREIGN KEY (`trip_id`) REFERENCES `trips` (`id`) ON DELETE SET NULL,
  CONSTRAINT `alerts_ibfk_2` FOREIGN KEY (`driver_id`) REFERENCES `drivers` (`id`) ON DELETE SET NULL,
  CONSTRAINT `alerts_ibfk_3` FOREIGN KEY (`vehicle_id`) REFERENCES `vehicles` (`id`) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- smart_truck.file_uploads
CREATE TABLE IF NOT EXISTS `file_uploads` (
  `id` int NOT NULL AUTO_INCREMENT,
  `original_filename` varchar(500) NOT NULL,
  `stored_filename` varchar(500) NOT NULL,
  `file_type` varchar(10) NOT NULL,
  `file_size_bytes` bigint DEFAULT NULL,
  `upload_type` varchar(50) NOT NULL,
  `status` varchar(20) DEFAULT 'pending',
  `total_records` int DEFAULT NULL,
  `records_processed` int DEFAULT '0',
  `records_failed` int DEFAULT '0',
  `error_summary` json DEFAULT NULL,
  `uploaded_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP,
  `processing_started_at` datetime DEFAULT NULL,
  `processing_completed_at` datetime DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_uploads_status` (`status`),
  KEY `idx_uploads_date` (`uploaded_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- smart_truck.driver_summary
CREATE TABLE IF NOT EXISTS `driver_summary` (
  `driver_id` int NOT NULL,
  `driver_name` varchar(255) DEFAULT NULL,
  `driver_mobile` varchar(20) DEFAULT NULL,
  `total_trips` int DEFAULT '0',
  `eta_met_count` int DEFAULT '0',
  `eta_known_count` int DEFAULT NULL,
  `eta_success_rate` decimal(6,2) DEFAULT '0.00',
  `avg_duration_min` decimal(12,2) DEFAULT NULL,
  `max_duration_min` decimal(12,2) DEFAULT NULL,
  `min_duration_min` decimal(12,2) DEFAULT NULL,
  `avg_speed_kmph` decimal(8,2) DEFAULT NULL,
  `vehicles_used` int DEFAULT '0',
  `total_distance_km` decimal(14,2) DEFAULT NULL,
  `avg_distance_km` decimal(10,2) DEFAULT NULL,
  `avg_eta_delay_min` decimal(12,2) DEFAULT NULL,
  `last_refreshed` timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `cnr_id` int NOT NULL DEFAULT '0',
  PRIMARY KEY (`driver_id`,`cnr_id`),
  CONSTRAINT `driver_summary_ibfk_1` FOREIGN KEY (`driver_id`) REFERENCES `drivers` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- smart_truck.vehicle_summary
CREATE TABLE IF NOT EXISTS `vehicle_summary` (
  `vehicle_id` int NOT NULL,
  `asset_id` varchar(50) DEFAULT NULL,
  `asset_type` varchar(100) DEFAULT NULL,
  `total_trips` int DEFAULT '0',
  `drivers_used` int DEFAULT '0',
  `avg_speed_kmph` decimal(8,2) DEFAULT NULL,
  `total_distance_km` decimal(14,2) DEFAULT NULL,
  `avg_distance_km` decimal(10,2) DEFAULT NULL,
  `eta_success_rate` decimal(6,2) DEFAULT NULL,
  `last_refreshed` timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `cnr_id` int NOT NULL DEFAULT '0',
  PRIMARY KEY (`vehicle_id`,`cnr_id`),
  CONSTRAINT `vehicle_summary_ibfk_1` FOREIGN KEY (`vehicle_id`) REFERENCES `vehicles` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- smart_truck.customer_summary
CREATE TABLE IF NOT EXISTS `customer_summary` (
  `customer_id` int NOT NULL,
  `customer_name` varchar(255) DEFAULT NULL,
  `total_trips` int DEFAULT '0',
  `total_distance_km` decimal(14,2) DEFAULT NULL,
  `avg_distance_km` decimal(10,2) DEFAULT NULL,
  `avg_duration_min` decimal(12,2) DEFAULT NULL,
  `eta_success_rate` decimal(6,2) DEFAULT NULL,
  `unique_routes` int DEFAULT '0',
  `unique_drivers` int DEFAULT '0',
  `unique_vehicles` int DEFAULT '0',
  `first_trip_date` date DEFAULT NULL,
  `last_trip_date` date DEFAULT NULL,
  `avg_trips_per_week` decimal(10,2) DEFAULT NULL,
  `top_origin` varchar(255) DEFAULT NULL,
  `top_destination` varchar(255) DEFAULT NULL,
  `last_refreshed` timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`customer_id`),
  CONSTRAINT `customer_summary_ibfk_1` FOREIGN KEY (`customer_id`) REFERENCES `customers` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- smart_truck.route_summary
CREATE TABLE IF NOT EXISTS `route_summary` (
  `id` int NOT NULL AUTO_INCREMENT,
  `origin` varchar(255) DEFAULT NULL,
  `destination` varchar(255) DEFAULT NULL,
  `route_name` varchar(510) DEFAULT NULL,
  `trip_count` int DEFAULT '0',
  `avg_duration_min` decimal(12,2) DEFAULT NULL,
  `avg_speed_kmph` decimal(8,2) DEFAULT NULL,
  `eta_success_rate` decimal(6,2) DEFAULT NULL,
  `avg_distance_km` decimal(10,2) DEFAULT NULL,
  `last_refreshed` timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `cnr_id` int NOT NULL DEFAULT '0',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_route` (`origin`,`destination`,`cnr_id`),
  KEY `idx_route_count` (`trip_count` DESC)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- smart_truck.route_time_patterns
CREATE TABLE IF NOT EXISTS `route_time_patterns` (
  `id` int NOT NULL AUTO_INCREMENT,
  `origin` varchar(255) DEFAULT NULL,
  `destination` varchar(255) DEFAULT NULL,
  `hour_of_day` tinyint DEFAULT NULL,
  `day_of_week` tinyint DEFAULT NULL,
  `avg_duration` decimal(12,2) DEFAULT NULL,
  `trip_count` int DEFAULT '0',
  `eta_success_rate` decimal(6,2) DEFAULT NULL,
  `last_refreshed` timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_route_time` (`origin`,`destination`,`hour_of_day`,`day_of_week`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- smart_truck.daily_fleet_stats
CREATE TABLE IF NOT EXISTS `daily_fleet_stats` (
  `stat_date` date NOT NULL,
  `total_trips` int DEFAULT '0',
  `total_distance_km` decimal(14,2) DEFAULT NULL,
  `avg_speed` decimal(8,2) DEFAULT NULL,
  `eta_success_rate` decimal(6,2) DEFAULT NULL,
  `active_drivers` int DEFAULT '0',
  `active_vehicles` int DEFAULT '0',
  `last_refreshed` timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `cnr_id` int NOT NULL DEFAULT '0',
  PRIMARY KEY (`stat_date`,`cnr_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- smart_truck.gps_waypoint_agg
CREATE TABLE IF NOT EXISTS `gps_waypoint_agg` (
  `cnr_id` int NOT NULL,
  `waypoint` varchar(255) NOT NULL,
  `state` varchar(10) DEFAULT NULL,
  `trips` int DEFAULT '0',
  `vehicles` int DEFAULT '0',
  `pings` bigint DEFAULT '0',
  `stopped_hours` decimal(12,1) DEFAULT '0.0',
  `moving_hours` decimal(12,1) DEFAULT '0.0',
  `dt_refreshed` timestamp NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`cnr_id`,`waypoint`),
  KEY `idx_wpagg_stopped` (`cnr_id`,`stopped_hours`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- smart_truck.gps_network_kpi
CREATE TABLE IF NOT EXISTS `gps_network_kpi` (
  `cnr_id` int NOT NULL,
  `waypoints` int DEFAULT '0',
  `states` int DEFAULT '0',
  `trips` int DEFAULT '0',
  `vehicles` int DEFAULT '0',
  `pings` bigint DEFAULT '0',
  `dt_refreshed` timestamp NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`cnr_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- smart_truck.gps_grid_agg
CREATE TABLE IF NOT EXISTS `gps_grid_agg` (
  `cnr_id` int NOT NULL,
  `mode` varchar(8) NOT NULL,
  `lat` decimal(6,2) NOT NULL,
  `lng` decimal(6,2) NOT NULL,
  `weight` bigint DEFAULT '0',
  PRIMARY KEY (`cnr_id`,`mode`,`lat`,`lng`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- smart_truck.gps_waypoint_hour_agg
CREATE TABLE IF NOT EXISTS `gps_waypoint_hour_agg` (
  `cnr_id` int NOT NULL,
  `waypoint` varchar(255) NOT NULL,
  `hour` tinyint NOT NULL,
  `stopped_min` int DEFAULT '0',
  PRIMARY KEY (`cnr_id`,`waypoint`,`hour`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- smart_truck.gps_state_agg
CREATE TABLE IF NOT EXISTS `gps_state_agg` (
  `cnr_id` int NOT NULL,
  `state` varchar(10) NOT NULL,
  `trips` int DEFAULT '0',
  `vehicles` int DEFAULT '0',
  `pings` bigint DEFAULT '0',
  `moving_hours` decimal(12,1) DEFAULT '0.0',
  `stopped_hours` decimal(12,1) DEFAULT '0.0',
  PRIMARY KEY (`cnr_id`,`state`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- smart_truck.tta_waypoints
CREATE TABLE IF NOT EXISTS `tta_waypoints` (
  `id` int NOT NULL AUTO_INCREMENT,
  `s_wpnt` varchar(255) NOT NULL,
  `s_state` varchar(10) DEFAULT NULL,
  `d_lat` decimal(10,8) DEFAULT NULL,
  `d_long` decimal(11,8) DEFAULT NULL,
  `first_seen` datetime DEFAULT NULL,
  `last_seen` datetime DEFAULT NULL,
  `dt_created` timestamp NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_waypoint_name` (`s_wpnt`),
  KEY `idx_waypoints_state` (`s_state`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- smart_truck.tta_waypoint_stats
CREATE TABLE IF NOT EXISTS `tta_waypoint_stats` (
  `waypoint_id` int NOT NULL,
  `total_trips` int DEFAULT '0',
  `total_vehicles` int DEFAULT '0',
  `total_pings` int DEFAULT '0',
  `stopped_pings` int DEFAULT '0',
  `moving_min` decimal(12,1) DEFAULT '0.0',
  `stopped_min` decimal(12,1) DEFAULT '0.0',
  `stop_events` int DEFAULT '0',
  `avg_stop_min` decimal(10,1) DEFAULT NULL,
  `longest_stop_min` decimal(10,1) DEFAULT NULL,
  `hour_profile` json DEFAULT NULL,
  `dow_profile` json DEFAULT NULL,
  `last_refreshed` timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`waypoint_id`),
  KEY `idx_wpstats_stopped` (`stopped_min` DESC)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- smart_truck.gps_speed_profile
CREATE TABLE IF NOT EXISTS `gps_speed_profile` (
  `i_trip_no` bigint NOT NULL,
  `s_zone` varchar(8) NOT NULL,
  `i_hour` tinyint NOT NULL,
  `i_kmph` smallint NOT NULL,
  `i_pings` int NOT NULL DEFAULT '0',
  `d_minutes` decimal(10,2) NOT NULL DEFAULT '0.00',
  `i_dist_m` bigint NOT NULL DEFAULT '0',
  PRIMARY KEY (`i_trip_no`,`s_zone`,`i_hour`,`i_kmph`),
  KEY `idx_speed_profile_zone` (`s_zone`,`i_kmph`,`i_trip_no`,`i_pings`,`d_minutes`,`i_dist_m`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- smart_truck.gps_speed_events
CREATE TABLE IF NOT EXISTS `gps_speed_events` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `i_trip_no` bigint NOT NULL,
  `i_floor_kmph` smallint NOT NULL,
  `s_zone` varchar(8) NOT NULL,
  `dt_start` datetime NOT NULL,
  `dt_end` datetime NOT NULL,
  `i_peak_kmph` smallint NOT NULL,
  `d_avg_kmph` decimal(5,1) NOT NULL,
  `d_minutes` decimal(8,2) NOT NULL DEFAULT '0.00',
  `i_dist_m` int NOT NULL DEFAULT '0',
  `i_pings` smallint NOT NULL DEFAULT '0',
  `d_lat` decimal(10,8) DEFAULT NULL,
  `d_long` decimal(11,8) DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_speed_ev_peak` (`s_zone`,`i_peak_kmph`),
  KEY `idx_speed_ev_trip` (`i_trip_no`),
  KEY `idx_speed_ev_start` (`dt_start`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- smart_truck.gps_speed_build
CREATE TABLE IF NOT EXISTS `gps_speed_build` (
  `s_key` varchar(32) NOT NULL,
  `i_rows` bigint NOT NULL DEFAULT '0',
  `i_trips` int NOT NULL DEFAULT '0',
  `i_pings` bigint NOT NULL DEFAULT '0',
  `d_seconds` decimal(8,2) NOT NULL DEFAULT '0.00',
  `s_detail` text,
  `dt_built` timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`s_key`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- smart_truck.gps_stop_events
CREATE TABLE IF NOT EXISTS `gps_stop_events` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `i_trip_no` bigint NOT NULL,
  `cnr_id` int DEFAULT NULL,
  `s_asset_id` varchar(50) DEFAULT NULL,
  `i_trans_id` varchar(50) DEFAULT NULL,
  `s_trans_name` varchar(255) DEFAULT NULL,
  `dt_start` datetime NOT NULL,
  `dt_end` datetime NOT NULL,
  `d_duration_min` decimal(10,1) NOT NULL,
  `i_hour` tinyint NOT NULL,
  `d_lat` decimal(10,8) NOT NULL,
  `d_long` decimal(11,8) NOT NULL,
  `i_spread_m` int DEFAULT NULL,
  `i_ping_count` int NOT NULL,
  `d_max_gap_min` decimal(6,1) DEFAULT NULL,
  `s_wpnt` varchar(255) DEFAULT NULL,
  `i_wpnt_mt` int DEFAULT NULL,
  `s_place_class` varchar(24) NOT NULL DEFAULT 'unclassified',
  `dt_created` timestamp NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_stop_event` (`i_trip_no`,`dt_start`),
  KEY `idx_stop_trip` (`i_trip_no`),
  KEY `idx_stop_geo` (`d_lat`,`d_long`),
  KEY `idx_stop_cnr_dur` (`cnr_id`,`d_duration_min`),
  KEY `idx_stop_class` (`s_place_class`),
  KEY `idx_stop_hour` (`i_hour`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- smart_truck.gps_stop_extract_log
CREATE TABLE IF NOT EXISTS `gps_stop_extract_log` (
  `i_trip_no` bigint NOT NULL,
  `i_ping_count` int NOT NULL,
  `i_stops_found` int NOT NULL DEFAULT '0',
  `dt_extracted` timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`i_trip_no`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- smart_truck.gps_stop_clusters
CREATE TABLE IF NOT EXISTS `gps_stop_clusters` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `cnr_id` int NOT NULL,
  `s_place_key` varchar(32) NOT NULL,
  `d_lat` decimal(10,8) NOT NULL,
  `d_long` decimal(11,8) NOT NULL,
  `i_radius_m` int DEFAULT NULL,
  `i_stops` int NOT NULL,
  `i_trips` int NOT NULL,
  `i_vehicles` int NOT NULL,
  `i_carriers` int NOT NULL,
  `d_median_dwell_min` decimal(10,1) DEFAULT NULL,
  `d_night_share` decimal(5,4) DEFAULT NULL,
  `d_night_share_adj` decimal(5,4) DEFAULT NULL,
  `i_median_isolation_m` int DEFAULT NULL,
  `s_nearest_node` varchar(255) DEFAULT NULL,
  `i_nearest_node_m` int DEFAULT NULL,
  `d_score` decimal(6,2) NOT NULL,
  `s_components` json DEFAULT NULL,
  `b_low_support` tinyint(1) NOT NULL DEFAULT '0',
  `s_amenity_hint` varchar(32) DEFAULT NULL,
  `dt_first_seen` datetime DEFAULT NULL,
  `dt_last_seen` datetime DEFAULT NULL,
  `dt_refreshed` timestamp NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_cluster_scope_score` (`cnr_id`,`d_score` DESC),
  KEY `idx_cluster_geo` (`d_lat`,`d_long`),
  KEY `idx_cluster_place` (`s_place_key`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- smart_truck.gps_stop_cluster_members
CREATE TABLE IF NOT EXISTS `gps_stop_cluster_members` (
  `cluster_id` bigint NOT NULL,
  `stop_event_id` bigint NOT NULL,
  PRIMARY KEY (`cluster_id`,`stop_event_id`),
  KEY `idx_member_stop` (`stop_event_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- smart_truck.gps_hotspot_labels
CREATE TABLE IF NOT EXISTS `gps_hotspot_labels` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `d_lat` decimal(10,8) NOT NULL,
  `d_long` decimal(11,8) NOT NULL,
  `s_status` varchar(24) NOT NULL,
  `s_note` varchar(1000) DEFAULT NULL,
  `s_labelled_by` varchar(100) DEFAULT NULL,
  `dt_created` timestamp NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_label_geo` (`d_lat`,`d_long`),
  KEY `idx_label_created` (`dt_created`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- smart_truck.tta_plant_delay_cache
CREATE TABLE IF NOT EXISTS `tta_plant_delay_cache` (
  `i_trip_no` bigint NOT NULL,
  `d_radius_km` decimal(6,2) NOT NULL DEFAULT '25.00',
  `i_benchmark_min` int NOT NULL DEFAULT '240',
  `s_gps_sig` varchar(64) DEFAULT NULL,
  `j_analysis` json NOT NULL,
  `j_insight` json DEFAULT NULL,
  `s_insight_source` varchar(30) DEFAULT NULL,
  `s_model` varchar(80) DEFAULT NULL,
  `dt_created` timestamp NULL DEFAULT CURRENT_TIMESTAMP,
  `dt_updated` timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`i_trip_no`,`d_radius_km`,`i_benchmark_min`)
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


-- smart_truck.app_settings
CREATE TABLE IF NOT EXISTS `app_settings` (
  `s_key` varchar(64) NOT NULL,
  `s_value` json NOT NULL,
  `dt_modified` timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`s_key`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
