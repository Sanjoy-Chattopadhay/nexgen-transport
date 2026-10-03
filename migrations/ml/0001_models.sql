-- nx_ml: the model registry and the prediction log (Smart-Truck's tables).


-- smart_truck.ml_models
CREATE TABLE IF NOT EXISTS `ml_models` (
  `id` int NOT NULL AUTO_INCREMENT,
  `model_name` varchar(100) NOT NULL,
  `version` int NOT NULL,
  `model_type` varchar(50) NOT NULL,
  `target_variable` varchar(100) NOT NULL,
  `metrics` json DEFAULT NULL,
  `feature_columns` json DEFAULT NULL,
  `hyperparameters` json DEFAULT NULL,
  `model_artifact_path` varchar(500) DEFAULT NULL,
  `training_data_count` int DEFAULT NULL,
  `training_data_start` datetime DEFAULT NULL,
  `training_data_end` datetime DEFAULT NULL,
  `is_active` tinyint(1) DEFAULT '0',
  `trained_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP,
  `notes` text,
  `validation_status` enum('passed','rejected','unchecked') NOT NULL DEFAULT 'unchecked',
  `validation_reasons` text,
  `validated_at` datetime DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_model_version` (`model_name`,`version`),
  KEY `idx_models_active` (`model_name`,`is_active`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- smart_truck.predictions
CREATE TABLE IF NOT EXISTS `predictions` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `model_id` int NOT NULL,
  `trip_id` int DEFAULT NULL,
  `driver_id` int DEFAULT NULL,
  `input_features` json NOT NULL,
  `predicted_value` decimal(12,4) DEFAULT NULL,
  `prediction_type` varchar(50) NOT NULL,
  `confidence_score` decimal(5,4) DEFAULT NULL,
  `actual_value` decimal(12,4) DEFAULT NULL,
  `prediction_error` decimal(12,4) DEFAULT NULL,
  `created_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `driver_id` (`driver_id`),
  KEY `idx_predictions_model_date` (`model_id`,`created_at` DESC),
  KEY `idx_predictions_trip` (`trip_id`),
  CONSTRAINT `predictions_ibfk_1` FOREIGN KEY (`model_id`) REFERENCES `ml_models` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
