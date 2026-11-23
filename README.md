# FilmsClub - Streaming Platform

A modern streaming platform built with React (frontend) and Python FastAPI (backend) with Telegram integration.

## 📋 Project Structure

```
filmsclub/
├── frontend-main/          # React + Vite frontend
├── filmsclub-backend/      # Python FastAPI backend
├── filmsclub-admin-panel/  # Admin panel
└── CDN-SETUP.md           # CDN configuration guide
```

## 🚀 Tech Stack

### Frontend
- **React 18** with Vite
- **NextUI** component library
- **TailwindCSS** for styling
- **React Router** for navigation
- **Axios** for API calls
- **Firebase** for authentication
- **Plyr** for video playback

### Backend
- **Python 3.10+**
- **FastAPI** web framework
- **Wzgram** (Telegram MTProto client — wzgram v3.1.1) for Telegram bot
- **MongoDB** (via Motor) for database
- **Redis** for caching
- **FFmpeg** for media processing
- **Docker** for containerization

## 📦 Prerequisites

- Node.js 18+ and npm
- Python 3.10+
- MongoDB (local or cloud)
- Redis (optional, for caching)
- FFmpeg installed on system
- Telegram Bot Token and API credentials
- MongoDB connection string

## 🔧 Environment Variables

### Backend (.env or config.env)

```env
# Required
API_ID=your_telegram_api_id
API_HASH=your_telegram_api_hash
BOT_TOKEN=your_bot_token
DATABASE=mongodb+srv://user:pass@cluster.mongodb.net/dbname

# Server Configuration
PORT=8000
BASE_URL=0.0.0.0
WEBSITE_URL=https://yourdomain.com

# Authentication
AUTH_CHANNEL=channel_id_1,channel_id_2
OWNER_ID=your_telegram_id
ADMIN_IDS=admin_id_1,admin_id_2
ADMIN_WEB_USERNAME=admin
ADMIN_WEB_PASSWORD=secure_password

# TMDB Configuration
TMDB_API=your_tmdb_api_key
IMDB_API=your_imdb_api_key
USE_TMDB=True

# Redis Configuration (Optional)
REDIS_URL=redis://localhost:6379/0
ENABLE_REDIS=true

# Cache Configuration
TRENDING_CACHE_TTL=160
SEARCH_CACHE_TTL=3000
MEDIA_INFO_CACHE_TTL=864000

# Telegram
TG_USERNAME=your_bot_username
UPDATE_CHANNEL=your_channel_username
```

### Frontend (.env)

```env
VITE_API_URL=https://your-backend-url.com
VITE_API_KEY=your_api_key
VITE_BASE_URL=https://yourdomain.com
VITE_TG_USERNAME=your_bot_username
VITE_SITENAME=FilmsClub
VITE_TG_URL=https://t.me/your_bot
```

## 🏠 Local Deployment

### 1. Clone the Repository

```bash
git clone https://github.com/yourusername/filmsclub.git
cd filmsclub
```

### 2. Backend Setup

```bash
cd filmsclub-backend

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Create .env file with your variables
cp .env.example .env
# Edit .env with your configuration

# Install FFmpeg (if not already installed)
# Ubuntu/Debian:
sudo apt-get update
sudo apt-get install ffmpeg

# macOS:
brew install ffmpeg

# Windows: Download from https://ffmpeg.org/download.html

# Start MongoDB (if using local)
# Ubuntu/Debian:
sudo systemctl start mongodb

# macOS:
brew services start mongodb-community

# Start Redis (optional, for caching)
# Ubuntu/Debian:
sudo systemctl start redis-server

# Run the backend
python -m Backend
```

The backend will start on `http://localhost:8000`

### 3. Frontend Setup

```bash
cd ../frontend-main

# Install dependencies
npm install

# Create .env file
cp .env.example .env
# Edit .env with your configuration

# Start development server
npm run dev
```

The frontend will start on `http://localhost:5173`

### 4. Admin Console Setup

The admin console is part of the frontend build (`frontend-main/src/admin`) at
`/admin`. It is **not** a separate app and has no separate dev server — it talks
to the backend's `/admin/api/*` routes.

1. Start the backend (`filmsclub-backend`) and the frontend (`npm run dev`).
2. Open `http://localhost:5173/admin`, log in with `ADMIN_WEB_USERNAME` /
   `ADMIN_WEB_PASSWORD`. In production, set `VITE_BASE_URL` to the backend
   origin before `npm run build`, or set it once in the login screen's
   connection settings.
3. If the console shows a **"Backend build is out of date"** banner, or pages
   report "Not Found" / "... unavailable" (Hero, Indexing, Orphans, Curation,
   Settings, Audit), the connected backend is an **older build**. Those routes
   exist in this source but not in the running process — a frontend-only
   redeploy cannot add them. The console now shows one shared, actionable
   notice with **Recheck** and **Backend connection** buttons instead of six
   separate 404s.

   **Cause:** the Heroku app is a separate git deploy unit. If
   `filmsclub-backend` has uncommitted work, `git push heroku main` ships the
   old commit (55 routes, no `/admin/api/*`). Commit first, then deploy:

   ```bash
   cd filmsclub-backend
   git status                 # must be clean
   git push heroku main
   heroku logs --tail -a <your-app-name>
   ```

   For a local backend, stop the old process and restart `python -m Backend`.
   Then verify the running process serves every route:

   ```bash
   cd filmsclub-backend
   python check_admin_api.py --url https://your-backend.example.com \
       --user admin --password 'your-password'
   ```

   It prints the backend's build marker and which required routes are missing.
   Expect build `2026-11-07` and every required route present. If they are all
   present but a page still fails, the console's backend URL is wrong — open
   the login screen's connection settings (`?conn=1`) and point it at the
   backend origin.

## Feature map (already in this source)

| Capability | Where |
| --- | --- |
| Add a file by IMDb/TMDB id + `t.me` post link, bypassing the filename parser | Bot `/manualadd <ref> <link>` (`Backend/helper/manual_add.py`), admin **Manual add** page, `POST /admin/api/queue/manual` |
| Attach that file to the right title | Queue worker merges by `tmdb_id` via `Database.insert_media` → `update_movie` / `update_tv_show`; series need `S01E02` |
| Multi-channel indexing | Send several `t.me` links to the bot in DM; concurrency cap = `index_max_concurrent_scans`; admin **Indexing** page channel checkboxes |
| "Skip how many newest messages?" prompt | Asked in the bot confirmation before a scan starts (presets + custom number), stored with the scan, survives restarts; also on the Indexing page and Runtime settings (`index_skip_first_messages` / `INDEX_SKIP_FIRST_MESSAGES`) |
| Reassign a mis-indexed file to the correct title / episode | Bot `/reassign <ref> <post link>`, admin **Manual add → Reassign existing**, and the **Move** button on each file in the media editor; `POST /admin/api/files/reassign` |
| Admin console | `/admin` — dashboard, media library + editor, manual add, hero, queue, failed files, indexing, curation, orphans, commands, cache, health, settings, audit |

If any of these look "missing" in a running deployment, the deployed backend is
stale — follow step 3 above rather than adding the feature again.

### Reassign: fixing a file indexed under the wrong title

`/manualadd` refuses a file that is already in the library, which does not help
when the automatic filename parser put it under the wrong movie or the wrong
episode. Reassign **moves** the existing quality entry instead of creating a
second one:

- Give the Telegram post link (or the stored file id from the media editor) and
  the correct IMDb/TMDB reference. The console looks up the wrong title first
  so you can confirm what will be moved.
- The encoded stream id is reproduced exactly, so every link the site already
  handed out keeps working. The Telegram post is never touched.
- Attach happens before detach. A failed attach changes nothing; a failed
  detach returns an explicit warning rather than losing the file.
- A same-quality file already on the target is **not** silently replaced —
  tick "Replace same-quality file" to allow it, or pick another quality.
- The wrong title is deleted if it has no files left (untick "Delete this title
  if it has no files left" to keep it).
- Series targets need `S01E02` / season + episode. Bot flags: `replace` and
  `keepempty`.

Bot example:

```
/reassign tt0133093 https://t.me/c/2685988600/42
/reassign https://www.imdb.com/title/tt0903747/ https://t.me/c/2685988600/42 S01E02 replace
```

Both surfaces call `Backend/helper/file_reassign.py`, so the panel and the bot
cannot diverge. Caches are cleared after a successful move.

**Channel indexing:** send one or more `t.me` post links to the bot in DM to
index whole channels concurrently (cap controlled by
`index_max_concurrent_scans`). Before starting, the confirmation asks how many
of the **newest messages to skip** — useful for pinned promos/welcome posts.
The answer applies to every channel in that batch, is stored with the scan, and
survives a restart. The default is `INDEX_SKIP_FIRST_MESSAGES` (env) or the
live `index_skip_first_messages` knob on the Runtime settings page. The
Indexing page of the console exposes the same field and shows skipped counts.

## ☁️ Heroku Deployment

### Option 1: Using Heroku CLI

#### Backend Deployment

```bash
# Install Heroku CLI
# https://devcenter.heroku.com/articles/heroku-cli

# Login to Heroku
heroku login

# Create a new app
heroku create your-app-name

# Navigate to backend directory
cd filmsclub-backend

# Initialize Git (if not already)
git init
git add .
git commit -m "Initial commit"

# Add Heroku remote
heroku git:remote -a your-app-name

# Set environment variables
heroku config:set API_ID=your_api_id -a your-app-name
heroku config:set API_HASH=your_api_hash -a your-app-name
heroku config:set BOT_TOKEN=your_bot_token -a your-app-name
heroku config:set DATABASE=your_mongodb_uri -a your-app-name
heroku config:set PORT=8000 -a your-app-name
heroku config:set BASE_URL=0.0.0.0 -a your-app-name
heroku config:set WEBSITE_URL=https://your-app-name.herokuapp.com -a your-app-name
heroku config:set TMDB_API=your_tmdb_api -a your-app-name
heroku config:set AUTH_CHANNEL=channel_id -a your-app-name
heroku config:set OWNER_ID=your_owner_id -a your-app-name
heroku config:set ADMIN_IDS=admin_id -a your-app-name
heroku config:set ADMIN_WEB_USERNAME=admin -a your-app-name
heroku config:set ADMIN_WEB_PASSWORD=secure_password -a your-app-name

# Add MongoDB addon (optional)
heroku addons:create mongolab:sandbox -a your-app-name

# Add Redis addon (optional)
heroku addons:create heroku-redis:hobby-dev -a your-app-name

# Deploy
git push heroku main

# Check logs
heroku logs --tail -a your-app-name
```

#### Frontend Deployment (Vercel recommended, see below)

For Heroku frontend deployment, you'll need to add a `Procfile`:

```bash
cd frontend-main

# Create Procfile
echo "web: npm run preview" > Procfile

# Build the app
npm run build

# Deploy
heroku create your-frontend-app
heroku buildpacks:set https://github.com/heroku/heroku-buildpack-nodejs.git
git push heroku main
```

### Option 2: Using Google Colab (One-Click Deploy)

Open the provided Colab notebook:
```
filmsclub-backend/heroku_deploy.ipynb
```

Follow the instructions in the notebook to deploy with a single click.

## 🌐 Vercel Deployment

### Frontend Deployment

```bash
# Install Vercel CLI
npm install -g vercel

# Navigate to frontend directory
cd frontend-main

# Login to Vercel
vercel login

# Deploy
vercel

# Follow the prompts:
# - Set project name
# - Link to existing project (if any)
# - Set build command: npm run build
# - Set output directory: dist
# - Add environment variables

# Deploy to production
vercel --prod
```

### Backend Deployment (Vercel not recommended for Python)

Vercel is primarily for frontend/static sites. For backend deployment on Vercel, consider:
1. Using Vercel Functions (limited Python support)
2. Deploying backend elsewhere (Heroku, Railway, Render) and connecting via API

**Recommended:** Use Heroku, Railway, or Render for the backend.

## 🖥️ VPS Deployment

### Prerequisites
- Ubuntu 20.04+ or similar Linux distribution
- Root or sudo access
- Domain name (optional)

### 1. Server Setup

```bash
# Update system
sudo apt update && sudo apt upgrade -y

# Install required packages
sudo apt install -y python3 python3-pip python3-venv nginx git curl

# Install FFmpeg
sudo apt install -y ffmpeg

# Install MongoDB
wget -qO - https://www.mongodb.org/static/pgp/server-6.0.asc | sudo apt-key add -
echo "deb [ arch=amd64,arm64 ] https://repo.mongodb.org/apt/ubuntu focal/mongodb-org/6.0 multiverse" | sudo tee /etc/apt/sources.list.d/mongodb-org-6.0.list
sudo apt update
sudo apt install -y mongodb-org
sudo systemctl start mongod
sudo systemctl enable mongod

# Install Redis
sudo apt install -y redis-server
sudo systemctl start redis-server
sudo systemctl enable redis-server
```

### 2. Clone and Setup Backend

```bash
# Clone repository
cd /var/www
sudo git clone https://github.com/yourusername/filmsclub.git
cd filmsclub/filmsclub-backend

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Create .env file
sudo nano .env
# Add your environment variables

# Test the application
python -m Backend
```

### 3. Setup Systemd Service

```bash
# Create systemd service file
sudo nano /etc/systemd/system/filmsclub.service
```

Add the following content:

```ini
[Unit]
Description=FilmsClub Backend
After=network.target

[Service]
User=www-data
WorkingDirectory=/var/www/filmsclub/filmsclub-backend
Environment="PATH=/var/www/filmsclub/filmsclub-backend/venv/bin"
ExecStart=/var/www/filmsclub/filmsclub-backend/venv/bin/python -m Backend
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
# Start and enable service
sudo systemctl start filmsclub
sudo systemctl enable filmsclub

# Check status
sudo systemctl status filmsclub
```

### 4. Setup Nginx Reverse Proxy

```bash
# Create Nginx configuration
sudo nano /etc/nginx/sites-available/filmsclub
```

Add the following configuration:

```nginx
server {
    listen 80;
    server_name yourdomain.com www.yourdomain.com;

    # Backend API
    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_cache_bypass $http_upgrade;
    }

    # Frontend (if serving from same server)
    location / {
        root /var/www/filmsclub/frontend-main/dist;
        try_files $uri $uri/ /index.html;
    }
}
```

```bash
# Enable site
sudo ln -s /etc/nginx/sites-available/filmsclub /etc/nginx/sites-enabled/

# Test Nginx configuration
sudo nginx -t

# Restart Nginx
sudo systemctl restart nginx
```

### 5. Setup SSL with Let's Encrypt

```bash
# Install Certbot
sudo apt install -y certbot python3-certbot-nginx

# Obtain SSL certificate
sudo certbot --nginx -d yourdomain.com -d www.yourdomain.com

# Auto-renewal is configured automatically
```

### 6. Build and Deploy Frontend

```bash
cd /var/www/filmsclub/frontend-main

# Install dependencies
npm install

# Build for production
npm run build

# The built files will be in /dist directory
# Nginx is already configured to serve them
```

### 7. Setup Firewall

```bash
# Allow SSH, HTTP, HTTPS
sudo ufw allow ssh
sudo ufw allow http
sudo ufw allow https
sudo ufw enable
```

### 8. Monitor and Logs

```bash
# View application logs
sudo journalctl -u filmsclub -f

# View Nginx logs
sudo tail -f /var/log/nginx/access.log
sudo tail -f /var/log/nginx/error.log

# View MongoDB logs
sudo tail -f /var/log/mongodb/mongod.log
```

## 🐳 Docker Deployment

### Backend Docker Deployment

```bash
cd filmsclub-backend

# Build Docker image
docker build -t filmsclub-backend .

# Run container
docker run -d \
  --name filmsclub-backend \
  -p 8000:8000 \
  --env-file .env \
  filmsclub-backend

# View logs
docker logs -f filmsclub-backend
```

### Docker Compose (Full Stack)

Create `docker-compose.yml` at root:

```yaml
version: '3.8'

services:
  backend:
    build: ./filmsclub-backend
    ports:
      - "8000:8000"
    environment:
      - API_ID=${API_ID}
      - API_HASH=${API_HASH}
      - BOT_TOKEN=${BOT_TOKEN}
      - DATABASE=${DATABASE}
      - REDIS_URL=redis://redis:6379/0
    depends_on:
      - mongodb
      - redis
    restart: unless-stopped

  frontend:
    build: ./frontend-main
    ports:
      - "80:80"
    depends_on:
      - backend
    restart: unless-stopped

  mongodb:
    image: mongo:6.0
    ports:
      - "27017:27017"
    volumes:
      - mongodb_data:/data/db
    restart: unless-stopped

  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    restart: unless-stopped

volumes:
  mongodb_data:
```

```bash
# Run all services
docker-compose up -d

# View logs
docker-compose logs -f

# Stop services
docker-compose down
```

## 🔒 Security Considerations

1. **Environment Variables**: Never commit `.env` files to version control
2. **API Keys**: Rotate API keys regularly
3. **HTTPS**: Always use HTTPS in production
4. **Firewall**: Configure firewall rules properly
5. **Authentication**: Use strong passwords for admin panel
6. **Rate Limiting**: Implement rate limiting on API endpoints
7. **CORS**: Configure CORS properly for your domain

## 📊 Monitoring and Maintenance

### Health Checks

```bash
# Check backend health
curl https://yourdomain.com/api/health

# Check MongoDB status
sudo systemctl status mongod

# Check Redis status
sudo systemctl status redis-server
```

### Backup Strategy

```bash
# MongoDB backup
mongodump --uri="mongodb://user:pass@host:port/db" --out=/backup/path

# Restore MongoDB
mongorestore --uri="mongodb://user:pass@host:port/db" /backup/path
```

### Log Rotation

Configure logrotate for application logs:

```bash
sudo nano /etc/logrotate.d/filmsclub
```

```
/var/log/filmsclub/*.log {
    daily
    rotate 7
    compress
    delaycompress
    missingok
    notifempty
    create 0640 www-data www-data
}
```

## 🐛 Troubleshooting

### Backend Issues

**Problem**: Backend won't start
```bash
# Check logs
sudo journalctl -u filmsclub -n 50

# Verify environment variables
heroku config -a your-app-name  # Heroku
printenv  # VPS
```

**Problem**: MongoDB connection failed
```bash
# Check MongoDB status
sudo systemctl status mongod

# Test connection
mongo mongodb://localhost:27017
```

**Problem**: FFmpeg not found
```bash
# Verify FFmpeg installation
ffmpeg -version

# Reinstall if needed
sudo apt install --reinstall ffmpeg
```

### Frontend Issues

**Problem**: Build fails
```bash
# Clear cache and reinstall
rm -rf node_modules package-lock.json
npm install
npm run build
```

**Problem**: API calls failing
- Check CORS configuration
- Verify API URL in `.env`
- Check backend logs for errors

### Deployment Issues

**Problem**: Heroku deployment fails
```bash
# Check build logs
heroku logs --tail -a your-app-name

# Verify dyno is running
heroku ps -a your-app-name
```

**Problem**: Vercel deployment fails
- Check build logs in Vercel dashboard
- Verify environment variables are set
- Ensure build command is correct

## 📚 Additional Resources

- [CDN Setup Guide](./CDN-SETUP.md) - Configure CloudFlare CDN for optimal performance
- [Backend README](./filmsclub-backend/README.md) - Backend-specific documentation
- [Frontend README](./frontend-main/README.md) - Frontend-specific documentation

## 🤝 Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## 📄 License

This project is licensed under the MIT License.

## 💬 Support

For support and questions:
- Open an issue on GitHub
- Join our Telegram channel: [Your Channel]
- Contact: [Your Email]

---

**Note**: Make sure to replace all placeholder values (API keys, URLs, etc.) with your actual configuration before deploying.
