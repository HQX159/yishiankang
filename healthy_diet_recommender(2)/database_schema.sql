-- MySQL 数据库架构 - 益食安康 糖尿病饮食管理系统
-- 由 SQLAlchemy 模型生成
-- 数据库: healthy_diet_recommender

-- 创建数据库
CREATE DATABASE IF NOT EXISTS healthy_diet_recommender
    CHARACTER SET utf8mb4
    COLLATE utf8mb4_unicode_ci;

USE healthy_diet_recommender;

-- =====================================================
-- 表: user (用户表)
-- =====================================================
CREATE TABLE IF NOT EXISTS `user` (
    `id` INT NOT NULL AUTO_INCREMENT,
    `username` VARCHAR(64) NOT NULL,
    `email` VARCHAR(120) NOT NULL,
    `password_hash` VARCHAR(128) NOT NULL,
    `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
    `updated_at` DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uq_user_username` (`username`),
    UNIQUE KEY `uq_user_email` (`email`),
    INDEX `idx_user_email` (`email`),
    INDEX `idx_user_username` (`username`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- =====================================================
-- Table: recipe
-- =====================================================
CREATE TABLE IF NOT EXISTS `recipe` (
    `id` INT NOT NULL AUTO_INCREMENT,
    `user_id` INT NOT NULL,
    `title` VARCHAR(255) NOT NULL,
    `ingredients` TEXT NOT NULL,
    `instructions` TEXT NOT NULL,
    `nutrition_info` TEXT,
    `cooking_time` INT,
    `servings` INT,
    `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
    `updated_at` DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    INDEX `idx_recipe_user_id` (`user_id`),
    INDEX `idx_recipe_title` (`title`),
    CONSTRAINT `fk_recipe_user_id` FOREIGN KEY (`user_id`)
        REFERENCES `user` (`id`) ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- =====================================================
-- Table: health_report
-- =====================================================
CREATE TABLE IF NOT EXISTS `health_report` (
    `id` INT NOT NULL AUTO_INCREMENT,
    `user_id` INT NOT NULL,
    `filename` VARCHAR(255) NOT NULL,
    `filepath` VARCHAR(500) NOT NULL,
    `content_text` TEXT,
    `uploaded_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    INDEX `idx_health_report_user_id` (`user_id`),
    INDEX `idx_health_report_uploaded_at` (`uploaded_at`),
    CONSTRAINT `fk_health_report_user_id` FOREIGN KEY (`user_id`)
        REFERENCES `user` (`id`) ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- =====================================================
-- 表: fridge_image (冰箱图片表)
-- =====================================================
CREATE TABLE IF NOT EXISTS `fridge_image` (
    `id` INT NOT NULL AUTO_INCREMENT,
    `user_id` INT NOT NULL,
    `filename` VARCHAR(255) NOT NULL,
    `filepath` VARCHAR(500) NOT NULL,
    `uploaded_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    INDEX `idx_fridge_image_user_id` (`user_id`),
    INDEX `idx_fridge_image_uploaded_at` (`uploaded_at`),
    CONSTRAINT `fk_fridge_image_user_id` FOREIGN KEY (`user_id`)
        REFERENCES `user` (`id`) ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- =====================================================
-- Table: ingredient
-- =====================================================
CREATE TABLE IF NOT EXISTS `ingredient` (
    `id` INT NOT NULL AUTO_INCREMENT,
    `fridge_image_id` INT NOT NULL,
    `name` VARCHAR(100) NOT NULL,
    `confidence` FLOAT,
    `detected_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    INDEX `idx_ingredient_fridge_image_id` (`fridge_image_id`),
    INDEX `idx_ingredient_name` (`name`),
    CONSTRAINT `fk_ingredient_fridge_image_id` FOREIGN KEY (`fridge_image_id`)
        REFERENCES `fridge_image` (`id`) ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- =====================================================
-- 表: favorite (收藏表)
-- =====================================================
CREATE TABLE IF NOT EXISTS `favorite` (
    `id` INT NOT NULL AUTO_INCREMENT,
    `user_id` INT NOT NULL,
    `recipe_id` INT NOT NULL,
    `favorited_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `unique_user_recipe` (`user_id`, `recipe_id`),
    INDEX `idx_favorite_user_id` (`user_id`),
    INDEX `idx_favorite_recipe_id` (`recipe_id`),
    CONSTRAINT `fk_favorite_user_id` FOREIGN KEY (`user_id`)
        REFERENCES `user` (`id`) ON DELETE CASCADE ON UPDATE CASCADE,
    CONSTRAINT `fk_favorite_recipe_id` FOREIGN KEY (`recipe_id`)
        REFERENCES `recipe` (`id`) ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- =====================================================
-- 实体关系说明
-- =====================================================
-- user (1) ----< (N) recipe
-- user (1) ----< (N) health_report
-- user (1) ----< (N) fridge_image
-- user (1) ----< (N) favorite
-- recipe (1) ----< (N) favorite
-- fridge_image (1) ----< (N) ingredient
