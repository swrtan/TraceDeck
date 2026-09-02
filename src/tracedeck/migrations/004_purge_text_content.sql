-- TraceDeck no longer retains prompt or assistant text. Existing content is
-- cleared during the next local database migration.
UPDATE turns SET user_prompt = NULL, assistant_response = NULL;
