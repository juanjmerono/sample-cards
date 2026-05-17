# REST Integration

## Overview
The application integrates with two external REST APIs for media content in Cards regions.

## REST Data Sources

### APEX_Youtube_Videos
- **Server**: `https://www.googleapis.com/youtube/v3/`
- **Path**: `videos`
- **Credential**: `google_api_key` (URL query string auth)
- **Operation**: GET with `fetchRows` database operation
- **Cache**: 10080 minutes (7 days), all users
- **Data Profile**: Row selector `items`

| Column | JSON Path | Type |
|---|---|---|
| ID | `id` | varchar2 |
| ETAG | `etag` | varchar2 |
| KIND | `kind` | varchar2 |
| CAPTION | `contentDetails.caption` | varchar2 |
| DURATION | `contentDetails.duration` | varchar2 |
| DIMENSION | `contentDetails.dimension` | varchar2 |
| DEFINITION | `contentDetails.definition` | varchar2 |
| PROJECTION | `contentDetails.projection` | varchar2 |
| LICENSEDCONTENT | `contentDetails.licensedContent` | varchar2 |

**Parameters**:
- `id`: default `77snU7P3ykA`
- `part`: default `contentDetails`

### Movie_Database
- **Server**: `https://api.themoviedb.org/3/`
- **Credential**: `api_key` (URL query string auth)
- Used for movie poster images and background images

### APEX_Youtube_Playlists / APEX_Youtube_PlayList_Items
- Additional YouTube REST sources for playlist functionality

## Pages Using REST Data Sources
| Page | Feature | APIs Used |
|---|---|---|
| 6 | Embedded Video | YouTube |
| 7 | Background Image | YouTube + TMDb |
| 10 | APEX Play List | YouTube |

## Credential Setup Required
These REST sources require manual credential configuration in the target workspace:
- **Google API Key**: credential name `key`, secret = user's API key
- **TMDb API Key**: credential name `api_key`, secret = user's API key

Substitution strings `GOOGLE_API` and `MOVIEDB_API` hold documentation URLs for obtaining keys.

## Server Definitions
- `www_googleapis_com_youtube_v3`: `https://www.googleapis.com/youtube/v3/`
- `api_themoviedb_org_3_list`: `https://api.themoviedb.org/3/`
