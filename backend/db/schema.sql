-- Core Files Metadata Table
CREATE TABLE IF NOT EXISTS files (
    id TEXT PRIMARY KEY,                       -- UUID v4
    filename TEXT NOT NULL,
    mime_type TEXT NOT NULL,
    file_category TEXT NOT NULL,               -- 'image', 'video', 'document', 'archive', 'other'
    size_bytes INTEGER NOT NULL,
    sha256 TEXT NOT NULL,                      -- Integrity verification checksum
    is_compressed INTEGER NOT NULL DEFAULT 0,
    compression_ratio REAL DEFAULT 1.0,
    is_lossy_video INTEGER NOT NULL DEFAULT 0,
    is_split INTEGER NOT NULL DEFAULT 0,       -- 1 if > 1.9 GB split into parts
    total_parts INTEGER NOT NULL DEFAULT 1,
    has_thumbnail INTEGER NOT NULL DEFAULT 0,
    thumbnail_path TEXT,
    exif_json TEXT,                            -- Camera & optics metadata
    taken_at TIMESTAMP,
    latitude REAL,
    longitude REAL,
    duration_seconds REAL,                     -- Video duration in seconds
    upload_status TEXT NOT NULL DEFAULT 'completed', -- 'completed', 'interrupted', 'uploading'
    nonce TEXT,                                -- Fresh 8-byte hex nonce for AES-CTR encryption
    encryption TEXT NOT NULL DEFAULT 'none',    -- 'none', 'ctr', 'gcm'
    is_deleted INTEGER NOT NULL DEFAULT 0,     -- Soft delete
    deleted_at TIMESTAMP,                      -- Moved to trash timestamp
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_files_created ON files(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_files_category ON files(file_category);
CREATE INDEX IF NOT EXISTS idx_files_deleted ON files(is_deleted);
CREATE INDEX IF NOT EXISTS idx_files_status ON files(upload_status);
CREATE INDEX IF NOT EXISTS idx_files_sha ON files(sha256);

-- Extent / Pointer-Offset Parts Table
CREATE TABLE IF NOT EXISTS file_parts (
    id TEXT PRIMARY KEY,                       -- UUID v4
    file_id TEXT NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    part_index INTEGER NOT NULL,               -- 0, 1, 2...
    byte_offset INTEGER NOT NULL,              -- Start byte offset in original stream
    part_size_bytes INTEGER NOT NULL,          -- Size of this slice in bytes
    part_sha256 TEXT NOT NULL,
    telegram_message_id INTEGER NOT NULL,      -- Channel message ID pointer
    telegram_channel_id TEXT NOT NULL DEFAULT '', -- Channel ID for multi-channel sharding
    telegram_file_id TEXT,                     -- MTProto unique document ID
    telegram_access_hash TEXT,                 -- MTProto document access hash
    upload_status TEXT NOT NULL DEFAULT 'confirmed',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(file_id, part_index)
);

-- High-performance covering index for range streaming & timeline seeking (eliminates table rowid scans)
CREATE INDEX IF NOT EXISTS idx_file_parts_covering ON file_parts(file_id, byte_offset, part_size_bytes, telegram_message_id, part_index);
CREATE INDEX IF NOT EXISTS idx_file_parts_tg ON file_parts(telegram_message_id);

-- Albums and Collections
CREATE TABLE IF NOT EXISTS albums (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    cover_file_id TEXT REFERENCES files(id) ON DELETE SET NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS file_albums (
    file_id TEXT NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    album_id TEXT NOT NULL REFERENCES albums(id) ON DELETE CASCADE,
    PRIMARY KEY(file_id, album_id)
);
CREATE INDEX IF NOT EXISTS idx_file_albums_reverse ON file_albums(album_id, file_id);

-- Tags
CREATE TABLE IF NOT EXISTS tags (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS file_tags (
    file_id TEXT NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    tag_id TEXT NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY(file_id, tag_id)
);
CREATE INDEX IF NOT EXISTS idx_file_tags_reverse ON file_tags(tag_id, file_id);

-- Expiring Share Links
CREATE TABLE IF NOT EXISTS share_links (
    id TEXT PRIMARY KEY,
    token TEXT NOT NULL UNIQUE,                -- Access bearer token
    file_id TEXT NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    password_hash TEXT,
    expires_at TIMESTAMP NOT NULL,
    max_views INTEGER,
    view_count INTEGER NOT NULL DEFAULT 0,
    is_revoked INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Active User Sessions
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,                       -- Session token
    expires_at TIMESTAMP NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Workstation Watch Folders
CREATE TABLE IF NOT EXISTS watch_folders (
    id TEXT PRIMARY KEY,
    path TEXT NOT NULL UNIQUE,
    label TEXT NOT NULL,
    target_album_id TEXT,
    auto_delete_local INTEGER DEFAULT 0,
    is_active INTEGER DEFAULT 1,
    last_synced_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS watch_folder_events (
    id TEXT PRIMARY KEY,
    folder_id TEXT NOT NULL REFERENCES watch_folders(id) ON DELETE CASCADE,
    file_path TEXT NOT NULL,
    file_size_bytes INTEGER NOT NULL,
    status TEXT NOT NULL,                      -- 'detected', 'syncing', 'synced', 'failed'
    file_id TEXT REFERENCES files(id) ON DELETE SET NULL,
    error_message TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_watch_events_folder ON watch_folder_events(folder_id, created_at DESC);
