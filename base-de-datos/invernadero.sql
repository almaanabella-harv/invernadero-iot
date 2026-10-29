-- =====================================================================
--  Invernadero IoT — Base de datos (MariaDB / MySQL)
--  Ejecutar como root:   sudo mariadb < invernadero.sql
--  o pegar el contenido en la pestaña SQL de phpMyAdmin.
-- =====================================================================

CREATE DATABASE IF NOT EXISTS invernadero
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

USE invernadero;

-- ---------------------------------------------------------------------
-- Lecturas de los sensores (una fila por medición)
-- Se usa TIMESTAMP (no DATETIME) para que Grafana muestre bien la hora
-- sin importar la zona horaria configurada en la VM.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS lecturas (
  id      INT UNSIGNED  NOT NULL AUTO_INCREMENT,
  fecha   TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  sensor  VARCHAR(30)   NOT NULL,          -- temperatura, humedad_ambiente, humedad_suelo, luz
  valor   DECIMAL(10,2) NOT NULL,
  PRIMARY KEY (id),
  INDEX idx_sensor_fecha (sensor, fecha)
) ENGINE=InnoDB;

-- ---------------------------------------------------------------------
-- Eventos de los actuadores (cada vez que se prende o apaga algo)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS eventos (
  id        INT UNSIGNED NOT NULL AUTO_INCREMENT,
  fecha     TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  actuador  VARCHAR(30)  NOT NULL,         -- riego, ventilador
  estado    ENUM('ON','OFF')        NOT NULL,
  origen    ENUM('auto','manual')   NOT NULL,
  motivo    VARCHAR(255) NULL,
  PRIMARY KEY (id),
  INDEX idx_actuador_fecha (actuador, fecha)
) ENGINE=InnoDB;

-- ---------------------------------------------------------------------
-- Alertas enviadas a n8n
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS alertas (
  id       INT UNSIGNED  NOT NULL AUTO_INCREMENT,
  fecha    TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  tipo     VARCHAR(40)   NOT NULL,         -- temperatura_critica, suelo_seco, sensores_desconectados
  sensor   VARCHAR(30)   NULL,
  valor    DECIMAL(10,2) NULL,
  mensaje  VARCHAR(255)  NOT NULL,
  PRIMARY KEY (id),
  INDEX idx_fecha (fecha)
) ENGINE=InnoDB;

-- ---------------------------------------------------------------------
-- Umbrales del sistema. Node-RED los vuelve a leer cada 30 segundos,
-- así que se pueden cambiar desde phpMyAdmin sin tocar el flow.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS configuracion (
  parametro    VARCHAR(40)   NOT NULL,
  valor        DECIMAL(10,2) NOT NULL,
  descripcion  VARCHAR(255)  NULL,
  PRIMARY KEY (parametro)
) ENGINE=InnoDB;

INSERT INTO configuracion (parametro, valor, descripcion) VALUES
  ('suelo_min',      30, 'Humedad del suelo (%) por debajo de la cual se ABRE el riego'),
  ('suelo_max',      60, 'Humedad del suelo (%) por encima de la cual se CIERRA el riego'),
  ('temp_max',       32, 'Temperatura (°C) por encima de la cual se PRENDE la ventilación'),
  ('temp_ok',        28, 'Temperatura (°C) por debajo de la cual se APAGA la ventilación'),
  ('temp_alerta',    38, 'Temperatura (°C) crítica: dispara alerta a n8n'),
  ('suelo_critico',  15, 'Humedad del suelo (%) crítica: dispara alerta a n8n'),
  ('alerta_minutos', 10, 'Minutos mínimos entre dos alertas del mismo tipo (anti-spam)')
ON DUPLICATE KEY UPDATE descripcion = VALUES(descripcion);

-- ---------------------------------------------------------------------
-- Usuarios
--   nodered → escribe y lee (lo usa el nodo mysql de Node-RED)
--   grafana → solo lectura (lo usa el datasource de Grafana)
-- Cambiar las contraseñas si el repo va a ser público.
-- ---------------------------------------------------------------------
-- Se crean para 'localhost' (Node-RED y Grafana en la misma VM) y para '%'
-- (por si Grafana corre en otra máquina de la red).
CREATE USER IF NOT EXISTS 'nodered'@'localhost' IDENTIFIED BY 'nodered123';
CREATE USER IF NOT EXISTS 'nodered'@'%'         IDENTIFIED BY 'nodered123';
GRANT SELECT, INSERT, UPDATE ON invernadero.* TO 'nodered'@'localhost';
GRANT SELECT, INSERT, UPDATE ON invernadero.* TO 'nodered'@'%';

CREATE USER IF NOT EXISTS 'grafana'@'localhost' IDENTIFIED BY 'grafana123';
CREATE USER IF NOT EXISTS 'grafana'@'%'         IDENTIFIED BY 'grafana123';
GRANT SELECT ON invernadero.* TO 'grafana'@'localhost';
GRANT SELECT ON invernadero.* TO 'grafana'@'%';

FLUSH PRIVILEGES;

-- ---------------------------------------------------------------------
-- Consultas útiles para la defensa
-- ---------------------------------------------------------------------
-- Últimas 20 lecturas:
--   SELECT * FROM lecturas ORDER BY id DESC LIMIT 20;
-- Resumen de las últimas 24 h:
--   SELECT sensor, MIN(valor), MAX(valor), ROUND(AVG(valor),1)
--   FROM lecturas WHERE fecha >= NOW() - INTERVAL 24 HOUR GROUP BY sensor;
-- Cuántas veces se prendió cada actuador hoy:
--   SELECT actuador, COUNT(*) FROM eventos
--   WHERE estado = 'ON' AND DATE(fecha) = CURDATE() GROUP BY actuador;
