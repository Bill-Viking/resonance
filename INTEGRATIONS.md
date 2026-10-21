# Provider integration research

Checked against primary documentation on 2026-09-13. These are development constraints, not connected capabilities. No provider API or background collector is implemented in the local edition.

| Provider | Verified constraint or capability | Product implication |
| --- | --- | --- |
| YouTube | `comments.list` selects comments by IDs or parent ID; it does not document a current-user authored-history filter. [Google reference](https://developers.google.com/youtube/v3/docs/comments/list) | An app must not promise that this endpoint lists every comment the user has ever written. A complete-history strategy requires separate proof. |
| X | User timeline retrieves authored posts, supports reply/repost filtering and requires developer app credentials. [X timeline documentation](https://docs.x.com/x-api/posts/timelines/introduction) | A candidate first integration once an approved developer app and suitable access are available. Retain replies and distinguish reposts. |
| LinkedIn | Personal social read permission is restricted to selected developers. [LinkedIn Comments API](https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/comments-api?view=li-lms-2026-04) | User sign-in alone does not grant permission to read all member activity. Do not promise personal comment coverage before approval. |
| TikTok | Display API documents profile data and uploaded videos through three read endpoints. [TikTok Display API](https://developers.tiktok.com/docs/en/display-api-overview) | It does not establish access to the user's comment history. A separate route must be proven. |
| Instagram | Meta's official API collection states Facebook Login API is for professional accounts and cannot access consumer accounts. [Meta API collection](https://www.postman.com/meta/instagram/folder/u4g5a2a/instagram-api-with-facebook-login) | Personal Instagram profiles need a supported alternative or explicit export workflow; do not silently ask users to convert account types. |
| Facebook | The direct Meta permissions documentation could not be fetched in this research pass. | Verify permitted personal-post/comment access and app review requirements before selecting a connection design. This remains unresolved. |
| Reddit | Reddit's public content policy distinguishes licensed use and unauthorized bulk access. [Reddit public content policy](https://support.reddithelp.com/hc/en-us/articles/26410290525844-Public-Content-Policy) | Obtain an approved access route for this general-use product; do not build a commercial promise around an unapproved scraper. |

No fees, permissions, platform changes or registrations have been accepted. Recheck these documents when implementation begins because availability and access rules change.
