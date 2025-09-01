-- Script de inicialización para la base de datos del Proyecto Titán
-- Dialecto: SQLite
-- Versión: 4.0 (Añadida tabla de Telemetría)

-- Borra las tablas si ya existen para asegurar un inicio limpio.
DROP TABLE IF EXISTS Telemetria;
DROP TABLE IF EXISTS ResumenEventos;
DROP TABLE IF EXISTS Sesiones;

--
-- Tabla: Sesiones
-- Almacena los datos generales de cada sesión de entrenamiento.
--
CREATE TABLE Sesiones (
    id_sesion INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre_archivo_origen TEXT NOT NULL UNIQUE,
    nombre_operador TEXT,
    nombre_clase TEXT,
    nombre_ejercicio TEXT,
    fecha_hora_inicio TEXT,
    duracion_segundos INTEGER,
    puntaje_final REAL,
    perfil_operador TEXT,
    fecha_carga TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

--
-- Tabla: ResumenEventos
-- Almacena los datos agregados de la tabla "Consolidated Results".
--
CREATE TABLE ResumenEventos (
    id_resumen INTEGER PRIMARY KEY AUTOINCREMENT,
    id_sesion INTEGER NOT NULL,
    tipo_evento TEXT,
    conteo_eventos INTEGER,
    recompensas REAL,
    penalizaciones REAL,
    FOREIGN KEY (id_sesion) REFERENCES Sesiones (id_sesion) ON DELETE CASCADE
);

--
-- Tabla: Telemetria (NUEVA)
-- Almacena las series de tiempo extraídas de los gráficos.
--
CREATE TABLE Telemetria (
    id_telemetria INTEGER PRIMARY KEY AUTOINCREMENT,
    id_sesion_fk INTEGER NOT NULL,
    nombre_grafico TEXT NOT NULL, -- ej: "Brake Pad", "Steering"
    timestamps TEXT NOT NULL,     -- ej: "0.0,0.48,0.96,..."
    valores TEXT NOT NULL,        -- ej: "0.03,0.0,0.0,..."
    FOREIGN KEY (id_sesion_fk) REFERENCES Sesiones (id_sesion) ON DELETE CASCADE
);


-- Índices para mejorar rendimiento de consultas
CREATE INDEX idx_sesiones_operador ON Sesiones(nombre_operador);
CREATE INDEX idx_sesiones_perfil ON Sesiones(perfil_operador);
CREATE INDEX idx_telemetria_sesion ON Telemetria(id_sesion_fk);
