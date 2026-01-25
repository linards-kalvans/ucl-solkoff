# Railway Deployment Guide

This guide will help you deploy the UCL Solkoff app to Railway's free tier.

## Prerequisites

1. A [Railway](https://railway.app) account (free tier available)
2. A GitHub account (for connecting your repository)
3. A football-data.org API key (get one at https://www.football-data.org/client/register)

## Deployment Steps

### 1. Prepare Your Repository

Make sure your code is pushed to a GitHub repository:

```bash
git add .
git commit -m "Prepare for Railway deployment"
git push origin main
```

### 2. Create a New Railway Project

1. Go to [Railway Dashboard](https://railway.app/dashboard)
2. Click "New Project"
3. Select "Deploy from GitHub repo"
4. Choose your repository
5. Railway will automatically detect the project

### 3. Configure Environment Variables

In Railway dashboard, go to your service → Variables tab and add:

**Required:**
- `EXTERNAL_API_KEY` - Your football-data.org API key
- `PORT` - Railway sets this automatically, but you can verify it's set

**Optional (with defaults):**
- `EXTERNAL_API_BASE_URL` - Default: `https://api.football-data.org/v4`
- `COMPETITION_ID` - Default: `CL` (Champions League)
- `DB_PATH` - Default: `./data/ucl.db` (Railway will use ephemeral storage)
- `UPDATE_INTERVAL` - Default: `3600` (1 hour in seconds)
- `API_CACHE_TTL` - Default: `3600` (1 hour in seconds)
- `API_MIN_REQUEST_INTERVAL` - Default: `0.1` (100ms)

### 4. Add Persistent Volume (Optional but Recommended)

For the free tier, Railway provides ephemeral storage. To persist your database:

1. Go to your service in Railway
2. Click "Add Volume"
3. Mount it to `/data` or your preferred path
4. Update `DB_PATH` environment variable to use the volume path

**Note:** Free tier has limited storage. Consider using Railway's persistent volume feature if available.

### 5. Deploy

Railway will automatically:
1. Detect the project (uses nixpacks)
2. Install dependencies using `uv sync`
3. Start the application with the command in `Procfile`

### 6. Access Your App

Once deployed:
1. Railway will provide a public URL (e.g., `https://your-app.railway.app`)
2. The frontend will be served at the root URL
3. API endpoints are available at `/api/*`

## Configuration Files

The deployment uses these files:
- `Procfile` - Defines the start command
- `nixpacks.toml` - Build configuration
- `railway.json` - Railway-specific configuration
- `pyproject.toml` - Python dependencies (used by uv)

## Troubleshooting

### Database Issues

If you see database errors:
- Check that the `data/` directory is writable
- On Railway, ensure volumes are properly mounted
- Check `DB_PATH` environment variable

### API Key Issues

If you see 403 errors:
- Verify `EXTERNAL_API_KEY` is set correctly
- Check Railway logs for API errors
- Ensure your API key is valid at football-data.org

### Build Failures

If the build fails:
- Check Railway logs for specific errors
- Ensure `uv` is available (nixpacks should handle this)
- Verify all dependencies in `pyproject.toml` are correct

### Port Issues

Railway automatically sets the `PORT` environment variable. The app is configured to use it:
```python
port = int(os.getenv("PORT", "8000"))
```

## Free Tier Limitations

Railway's free tier includes:
- 500 hours/month of usage
- Limited storage (ephemeral by default)
- Public deployments

**Important:** The database will be reset if you don't use a persistent volume. Consider upgrading or using Railway's volume feature for data persistence.

## Monitoring

- Check Railway dashboard for logs
- Use `/api/health` endpoint for health checks
- Monitor API rate limits (football-data.org has rate limits)

## Updating the Deployment

To update your deployment:
1. Push changes to GitHub
2. Railway will automatically redeploy
3. Or manually trigger a redeploy from Railway dashboard

## Custom Domain (Optional)

Railway allows custom domains:
1. Go to your service → Settings → Domains
2. Add your custom domain
3. Configure DNS as instructed

