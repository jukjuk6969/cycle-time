--
-- PostgreSQL database dump
--

\restrict r9r1p2qPlLyTcPIpZIDCuAu1vfBgYw50eU18cIcLAuS7Jb7ydfyy09TnpSSfJUe

-- Dumped from database version 18.6
-- Dumped by pg_dump version 18.6

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: cycle_times; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.cycle_times (
    line integer NOT NULL,
    station_id integer NOT NULL,
    end_timestamp timestamp without time zone NOT NULL,
    start_timestamp timestamp without time zone,
    operator_id text,
    cycle_time_sec double precision NOT NULL,
    cycle_count integer DEFAULT 0 NOT NULL,
    is_complete boolean DEFAULT true NOT NULL,
    is_anomaly boolean DEFAULT false NOT NULL,
    anomaly_type text,
    is_genesis boolean DEFAULT false NOT NULL,
    is_buffered boolean DEFAULT false NOT NULL,
    video_filename text,
    video_size_mb double precision,
    video_recorded_at timestamp without time zone,
    video_label text,
    created_at timestamp without time zone DEFAULT timezone('UTC'::text, now()) NOT NULL,
    target_limit_sec double precision,
    short_limit_sec double precision,
    anomaly_label text,
    skipped_expected text,
    video_type character varying(10)
);


--
-- Name: operators; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.operators (
    id text NOT NULL,
    name text,
    shift text,
    created_at timestamp without time zone DEFAULT timezone('UTC'::text, now()) NOT NULL
);


--
-- Name: stations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.stations (
    line integer NOT NULL,
    station_id integer NOT NULL,
    name text,
    ip text,
    port integer DEFAULT 5001,
    hostname text,
    takt_time double precision,
    target_cycle_time double precision,
    roi_timeout_sec double precision,
    min_cycle_sec double precision DEFAULT 0,
    short_anomaly_sec double precision DEFAULT 0,
    is_active boolean DEFAULT true NOT NULL,
    is_online boolean DEFAULT false NOT NULL,
    last_seen timestamp without time zone
);


--
-- Name: cycle_times pk_cycle_times; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cycle_times
    ADD CONSTRAINT pk_cycle_times PRIMARY KEY (line, station_id, end_timestamp);


--
-- Name: operators pk_operators; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.operators
    ADD CONSTRAINT pk_operators PRIMARY KEY (id);


--
-- Name: stations pk_stations; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.stations
    ADD CONSTRAINT pk_stations PRIMARY KEY (line, station_id);


--
-- Name: cycle_times_end_timestamp_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX cycle_times_end_timestamp_idx ON public.cycle_times USING brin (end_timestamp);


--
-- Name: ix_cycle_times_end; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_cycle_times_end ON public.cycle_times USING btree (end_timestamp) INCLUDE (line, station_id, cycle_time_sec, is_complete);


--
-- Name: ix_cycle_times_stats; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_cycle_times_stats ON public.cycle_times USING btree (line, station_id, end_timestamp) INCLUDE (cycle_time_sec, is_complete);


--
-- Name: ix_cycle_times_vid_end; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_cycle_times_vid_end ON public.cycle_times USING btree (end_timestamp) WHERE ((video_filename IS NOT NULL) AND (is_complete = true));


--
-- Name: ix_cycle_times_video; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_cycle_times_video ON public.cycle_times USING btree (video_filename);


--
-- Name: ix_stations_ip; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_stations_ip ON public.stations USING btree (ip);


--
-- Name: SCHEMA public; Type: ACL; Schema: -; Owner: -
--

GRANT USAGE ON SCHEMA public TO dev;


--
-- Name: TABLE cycle_times; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON TABLE public.cycle_times TO dev;


--
-- Name: TABLE operators; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON TABLE public.operators TO dev;


--
-- Name: TABLE stations; Type: ACL; Schema: public; Owner: -
--

GRANT ALL ON TABLE public.stations TO dev;


--
-- Name: DEFAULT PRIVILEGES FOR SEQUENCES; Type: DEFAULT ACL; Schema: public; Owner: -
--

ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public GRANT ALL ON SEQUENCES TO dev;


--
-- Name: DEFAULT PRIVILEGES FOR TABLES; Type: DEFAULT ACL; Schema: public; Owner: -
--

ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public GRANT ALL ON TABLES TO dev;


--
-- PostgreSQL database dump complete
--

\unrestrict r9r1p2qPlLyTcPIpZIDCuAu1vfBgYw50eU18cIcLAuS7Jb7ydfyy09TnpSSfJUe

