ALTER TABLE turns ADD COLUMN prompt_original_size_bytes INTEGER;
ALTER TABLE turns ADD COLUMN response_original_size_bytes INTEGER;
ALTER TABLE tool_calls ADD COLUMN arguments_original_size_bytes INTEGER;

