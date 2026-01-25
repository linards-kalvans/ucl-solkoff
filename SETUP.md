# Setup Guide

## API Key Configuration

The application requires an API key from [football-data.org](https://www.football-data.org/) to fetch Champions League data.

### Getting an API Key

1. Go to https://www.football-data.org/client/register
2. Create a free account
3. After registration, you'll receive an API token
4. The free tier allows 10 requests per minute

### Setting Up the API Key

1. Create a `.env` file in the project root (if it doesn't exist):
   ```bash
   cp .env.example .env
   ```

2. Edit the `.env` file and add your API key:
   ```
   EXTERNAL_API_KEY=your_actual_api_key_here
   ```

3. The `.env` file should look like this:
   ```
   # External API Configuration
   EXTERNAL_API_KEY=your_actual_api_key_here
   EXTERNAL_API_BASE_URL=https://api.football-data.org/v4
   COMPETITION_ID=CL

   # Server Configuration
   PORT=8000

   # Database Configuration
   DB_PATH=./data/ucl.db

   # Scheduler Configuration
   UPDATE_INTERVAL=3600
   ```

### Verifying Your API Key

After setting up your API key, restart the backend server. You should see:
- No 403 errors in the logs
- Successful data sync messages
- Data appearing in the database

### Troubleshooting

**Error: 403 Forbidden**
- Your API key is missing, invalid, or expired
- Check that `EXTERNAL_API_KEY` is set correctly in `.env`
- Verify your API key at https://www.football-data.org/client/register
- Make sure there are no extra spaces or quotes around the key

**Error: Rate Limit Exceeded**
- Free tier allows 10 requests per minute
- The scheduler runs every hour by default
- You can increase `UPDATE_INTERVAL` in `.env` to reduce frequency

**Error: API key is required**
- The `.env` file is missing or `EXTERNAL_API_KEY` is not set
- Make sure the `.env` file is in the project root directory
- Check that the variable name is exactly `EXTERNAL_API_KEY`

### Testing Without API Key

If you want to test the application without an API key, you can:
1. Comment out the scheduler initialization in `backend/main.py`
2. Manually populate the database with test data
3. The API endpoints will still work, but will return empty results

