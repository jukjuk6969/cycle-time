CREATE TABLE cycle_times (
    station_id        integer           NOT NULL,
    line              integer           NOT NULL,
    operator_id       varchar(100),
    cycle_time_sec    double precision  NOT NULL,
    start_timestamp   timestamp         NOT NULL,   -- UTC
    end_timestamp     timestamp         NOT NULL,   -- UTC (WIB = +7)
    is_complete       boolean           NOT NULL DEFAULT false,
    is_anomaly        boolean           NOT NULL DEFAULT false,
    anomaly_type      varchar(10),                  -- OK / LONG / SHORT
    is_genesis        boolean           NOT NULL DEFAULT false,
    is_buffered       boolean           NOT NULL DEFAULT false,
    cycle_count       integer           NOT NULL DEFAULT 0,
    video_filename    varchar(260),
    video_size_mb     double precision,
    video_recorded_at timestamp,
    video_label       varchar(50),
    target_limit_sec  double precision,
    short_limit_sec   double precision,
    skipped_expected  varchar(500),
    anomaly_label     varchar(300),
    created_at        timestamp         NOT NULL DEFAULT timezone('UTC', now()),

    CONSTRAINT pk_cycle_times PRIMARY KEY (line, station_id, end_timestamp)
);

-- Statistik/summary lintas station + index-only scan
CREATE INDEX ix_cycle_times_end
    ON cycle_times (end_timestamp)
    INCLUDE (line, station_id, cycle_time_sec, is_complete);

-- Pencarian per nama file video
CREATE INDEX ix_cycle_times_video
    ON cycle_times (video_filename);

-- getVideos rentang waktu lebar (hasil tes: 2.098 ms -> 38 ms)
CREATE INDEX ix_cycle_times_vid_end
    ON cycle_times (end_timestamp)
    WHERE video_filename IS NOT NULL AND is_complete = true;

-- getStationStats / getBucketStats rentang panjang (hasil tes: 1.890 ms -> 513 ms)
CREATE INDEX ix_cycle_times_stats
    ON cycle_times (line, station_id, end_timestamp)
    INCLUDE (cycle_time_sec, is_complete);

-- Opsional: idx_scan = 0, kandidat dihapus setelah data besar
CREATE INDEX cycle_times_end_timestamp_idx
    ON cycle_times USING brin (end_timestamp);
