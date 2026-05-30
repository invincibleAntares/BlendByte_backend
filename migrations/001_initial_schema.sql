-- BlendByte Database Schema
-- Run this in your Supabase SQL Editor

-- Enable UUID extension
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Users table
CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    clerk_user_id TEXT UNIQUE NOT NULL,
    email TEXT NOT NULL,
    name TEXT,
    last_login TIMESTAMPTZ,
    total_searches INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Sessions table (gift search sessions)
CREATE TABLE IF NOT EXISTS sessions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    recipient_profile JSONB,
    search_queries JSONB,
    products_returned JSONB,
    budget_stated INTEGER,
    budget_searched INTEGER,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Clicks table (affiliate link tracking)
CREATE TABLE IF NOT EXISTS clicks (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    session_id UUID REFERENCES sessions(id) ON DELETE SET NULL,
    product_asin TEXT NOT NULL,
    clicked_at TIMESTAMPTZ DEFAULT NOW()
);

-- Logs table (error logging)
CREATE TABLE IF NOT EXISTS logs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    error_message TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes for better query performance
CREATE INDEX IF NOT EXISTS idx_users_clerk_user_id ON users(clerk_user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_created_at ON sessions(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_clicks_user_id ON clicks(user_id);
CREATE INDEX IF NOT EXISTS idx_clicks_session_id ON clicks(session_id);
CREATE INDEX IF NOT EXISTS idx_logs_user_id ON logs(user_id);
CREATE INDEX IF NOT EXISTS idx_logs_created_at ON logs(created_at DESC);

-- Function to increment user search count
CREATE OR REPLACE FUNCTION increment_searches(user_uuid UUID)
RETURNS VOID AS $$
BEGIN
    UPDATE users
    SET total_searches = total_searches + 1
    WHERE id = user_uuid;
END;
$$ LANGUAGE plpgsql;

-- Add comments for documentation
COMMENT ON TABLE users IS 'Stores user account information from Clerk authentication';
COMMENT ON TABLE sessions IS 'Stores each gift search session with all agent outputs';
COMMENT ON TABLE clicks IS 'Tracks affiliate link clicks for commission tracking';
COMMENT ON TABLE logs IS 'Application error logs for debugging and monitoring';
