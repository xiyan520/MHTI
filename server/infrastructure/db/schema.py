"""Database schema definitions - all table creation statements."""

import aiosqlite


async def create_all_tables(db: aiosqlite.Connection) -> None:
    """Create all database tables."""
    await _create_core_tables(db)
    await _create_auth_tables(db)
    await _create_job_tables(db)
    await _create_watcher_tables(db)
    await _create_log_tables(db)


async def _create_core_tables(db: aiosqlite.Connection) -> None:
    """Create core configuration tables."""
    # Config table - application settings
    await db.execute("""
        CREATE TABLE IF NOT EXISTS config (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key TEXT UNIQUE NOT NULL,
            value TEXT NOT NULL,
            encrypted INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Auth config table - JWT secret and auth settings
    await db.execute("""
        CREATE TABLE IF NOT EXISTS auth_config (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key TEXT UNIQUE NOT NULL,
            value TEXT NOT NULL,
            encrypted INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)


async def _create_auth_tables(db: aiosqlite.Connection) -> None:
    """Create authentication related tables."""
    # Admin table
    await db.execute("""
        CREATE TABLE IF NOT EXISTS admin (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            avatar TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # 迁移: 为旧数据库添加 avatar 字段
    cursor = await db.execute("PRAGMA table_info(admin)")
    columns = {row[1] for row in await cursor.fetchall()}
    if "avatar" not in columns:
        await db.execute("ALTER TABLE admin ADD COLUMN avatar TEXT")

    # Sessions table
    await db.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            refresh_token_hash TEXT NOT NULL,
            -- 轮换宽限：旧 refresh token 在新 token 签发后的短时间内仍然可用，
            -- 否则多标签页同时刷新会把对方踢下线
            prev_refresh_token_hash TEXT,
            prev_valid_until TIMESTAMP,
            device_name TEXT,
            device_type TEXT DEFAULT 'desktop',
            ip_address TEXT,
            user_agent TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP NOT NULL,
            last_used_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # 迁移: 为旧数据库补上 refresh token 轮换所需的列
    cursor = await db.execute("PRAGMA table_info(sessions)")
    session_columns = {row[1] for row in await cursor.fetchall()}
    for column, ddl in (
        ("prev_refresh_token_hash", "TEXT"),
        ("prev_valid_until", "TIMESTAMP"),
    ):
        if column not in session_columns:
            await db.execute(f"ALTER TABLE sessions ADD COLUMN {column} {ddl}")

    # Login history table
    await db.execute("""
        CREATE TABLE IF NOT EXISTS login_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            ip_address TEXT,
            user_agent TEXT,
            device_name TEXT,
            login_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            success INTEGER NOT NULL DEFAULT 0,
            failure_reason TEXT,
            session_id TEXT
        )
    """)

    # Login attempts table
    await db.execute("""
        CREATE TABLE IF NOT EXISTS login_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_ip TEXT NOT NULL,
            attempts INTEGER DEFAULT 0,
            last_attempt TIMESTAMP,
            UNIQUE(client_ip)
        )
    """)


async def _create_job_tables(db: aiosqlite.Connection) -> None:
    """Create job and task related tables."""
    # Manual jobs table
    await db.execute("""
        CREATE TABLE IF NOT EXISTS manual_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_path TEXT NOT NULL,
            target_folder TEXT NOT NULL,
            metadata_dir TEXT DEFAULT '',
            link_mode INTEGER NOT NULL DEFAULT 2,
            delete_empty_parent INTEGER DEFAULT 1,
            config_reuse_id INTEGER,
            source TEXT DEFAULT 'manual',
            advanced_settings TEXT,
            scan_locator TEXT,
            target_locator TEXT,
            metadata_locator TEXT,
            allow_local_output INTEGER DEFAULT 0,
            created_at TEXT NOT NULL,
            started_at TEXT,
            finished_at TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            success_count INTEGER DEFAULT 0,
            skip_count INTEGER DEFAULT 0,
            error_count INTEGER DEFAULT 0,
            total_count INTEGER DEFAULT 0,
            error_message TEXT
        )
    """)

    # Scheduled tasks table
    await db.execute("""
        CREATE TABLE IF NOT EXISTS scheduled_tasks (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            folder_path TEXT NOT NULL,
            cron_expression TEXT NOT NULL,
            enabled INTEGER DEFAULT 1,
            last_run TEXT,
            next_run TEXT,
            created_at TEXT NOT NULL
        )
    """)

    # History records table
    await db.execute("""
        CREATE TABLE IF NOT EXISTS history_records (
            id TEXT PRIMARY KEY,
            task_name TEXT NOT NULL,
            folder_path TEXT NOT NULL,
            executed_at TEXT NOT NULL,
            status TEXT NOT NULL,
            total_files INTEGER NOT NULL,
            success_count INTEGER NOT NULL,
            failed_count INTEGER NOT NULL,
            duration_seconds REAL NOT NULL,
            error_message TEXT,
            manual_job_id INTEGER,
            title TEXT,
            original_title TEXT,
            plot TEXT,
            tags TEXT,
            cover_url TEXT,
            poster_url TEXT,
            thumb_url TEXT,
            release_date TEXT,
            rating REAL
        )
    """)

    # Scrape jobs table
    await db.execute("""
        CREATE TABLE IF NOT EXISTS scrape_jobs (
            id TEXT PRIMARY KEY,
            file_path TEXT NOT NULL,
            output_dir TEXT NOT NULL,
            metadata_dir TEXT,
            link_mode TEXT,
            source TEXT NOT NULL DEFAULT 'manual',
            source_id INTEGER,
            advanced_settings TEXT,
            file_locator TEXT,
            output_locator TEXT,
            metadata_locator TEXT,
            allow_local_output INTEGER DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL,
            started_at TEXT,
            finished_at TEXT,
            error_message TEXT,
            history_record_id TEXT
        )
    """)

    # Scraped files table
    await db.execute("""
        CREATE TABLE IF NOT EXISTS scraped_files (
            id TEXT PRIMARY KEY,
            source_path TEXT NOT NULL UNIQUE,
            target_path TEXT,
            file_size INTEGER NOT NULL,
            tmdb_id INTEGER,
            season INTEGER,
            episode INTEGER,
            title TEXT,
            scraped_at TEXT NOT NULL,
            history_record_id TEXT
        )
    """)
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_scraped_files_source ON scraped_files(source_path)"
    )


async def _create_watcher_tables(db: aiosqlite.Connection) -> None:
    """Create file watcher related tables."""
    # Watched folders table
    await db.execute("""
        CREATE TABLE IF NOT EXISTS watched_folders (
            id TEXT PRIMARY KEY,
            path TEXT NOT NULL UNIQUE,
            enabled INTEGER DEFAULT 1,
            mode TEXT DEFAULT 'realtime',
            scan_interval_seconds INTEGER DEFAULT 60,
            file_stable_seconds INTEGER DEFAULT 30,
            auto_scrape INTEGER DEFAULT 1,
            output_dir TEXT,
            provider TEXT DEFAULT 'local',
            file_id TEXT,
            last_scan TEXT,
            created_at TEXT NOT NULL
        )
    """)


async def _create_log_tables(db: aiosqlite.Connection) -> None:
    """Create logging related tables."""
    # Logs table - application logs storage
    await db.execute("""
        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            level TEXT NOT NULL,
            logger TEXT NOT NULL,
            message TEXT NOT NULL,
            extra_data TEXT,
            request_id TEXT,
            user_id INTEGER
        )
    """)

    # Create indexes for efficient querying
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_logs_timestamp ON logs(timestamp)"
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_logs_level ON logs(level)"
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_logs_logger ON logs(logger)"
    )

    # Log config table - singleton configuration
    await db.execute("""
        CREATE TABLE IF NOT EXISTS log_config (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            log_level TEXT DEFAULT 'INFO',
            console_enabled INTEGER DEFAULT 1,
            file_enabled INTEGER DEFAULT 1,
            db_enabled INTEGER DEFAULT 1,
            max_file_size_mb INTEGER DEFAULT 10,
            max_file_count INTEGER DEFAULT 5,
            db_retention_days INTEGER DEFAULT 30,
            realtime_enabled INTEGER DEFAULT 1
        )
    """)

    # Insert default config if not exists
    await db.execute("""
        INSERT OR IGNORE INTO log_config (id) VALUES (1)
    """)
