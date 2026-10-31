# 安卓客户端与 fpk 的功能对照

> 维护约定：新增/修改安卓端接口时同步更新本表；「已验证」列只认真机跑通，
> CI 通过（编译 + 签名 + 3DES 对拍）不等于功能可用。

## 已实现

| 模块 | 安卓实现 | 上游接口 | 真机验证 |
| --- | --- | --- | --- |
| 扫码登录（QQ/微信）、手机验证码 | `net/LoginApi.kt` | ptlogin / open.weixin / SendPhoneAuthCode | 未验证 |
| 搜索单曲 | `net/MusicApi.search` | musicu.fcg DoSearchForQQMusicDesktop | 未验证 |
| 播放（标准/高品质/无损） | `net/MusicApi.songUrl`、`net/Lossless.kt` | vkey.GetVkey / GetEVkey + QMC2 | 未验证 |
| 逐字歌词 QRC | `net/TripleDes.kt`、`net/LyricApi.kt` | GetPlayLyricInfo | 3DES 有单测，XML 解析未验证 |
| 首页-为你推荐（信息流） | `net/RecommendApi.homeFeed` | RecommendFeed/get_recommend_feed | 未验证 |
| 首页-推荐歌单 | `net/RecommendApi.songlists` | PlaylistSquare/GetRecommendFeed | 未验证 |
| 首页-新歌速递 | `net/RecommendApi.newSongs` | NewSongServer/get_new_song_info | 未验证 |
| 首页-每日推荐 | `net/RecommendApi.radar` | TrackRelationServer/GetRadarSong | 未验证 |
| 首页-新碟上架 | `net/AlbumApi` | NewAlbumServer / AlbumSongList | 未验证 |
| 首页-热门歌手 | `net/SingerApi` | musichallSinger.SingerList / GetSingerSongList | 未验证 |
| 首页-热搜榜 | `net/MusicApi.hotKeys` | HotkeyService/GetHotkeyForQQMusicMobile | 未验证 |
| 排行榜 + 猜你喜欢 | `net/TopApi`、`net/RecommendApi.guess` | musicToplist.Toplist / radioProxy | 未验证 |
| 我的歌单 / 我喜欢 | `net/PlaylistApi` | GetPlaylistByUin / CgiGetDiss(dirid=201) | 未验证 |
| 收藏到我喜欢 / 加到歌单 | `net/PlaylistApi.addSong` | PlaylistDetailWrite/AddSonglist | 未验证 |
| 下载到手机（SAF） | `net/Downloader.kt`、`net/DirStore.kt`、`net/Tagger.kt` | — | 未验证 |
| 播放队列 / 自动续播 / 下载历史 | `ui/AppRoot.kt`、`net/History.kt` | 本地 | 未验证 |

## 与 fpk 的差异（刻意不做）

- **每日 30 首**：fpk 是靠搜索公开歌单再挑一个日更的，安卓端已有「每日推荐（私人雷达）」，功能重叠，不实现。
- **消息推送**：依赖 NAS 上的推送网关，手机端无意义。
- **相似歌曲**：fpk 在播放页展示；安卓端暂缺，需要 `music.similarity.SongSimilarity`，可按需补。
- **首页板块开关 / 数量上限**：fpk 的设置项，安卓端固定按接口默认数量取。

## 高风险待验证点

1. `LyricApi.parseQrc` —— QRC XML 的行/字时间轴字段名是按 QQ 的 QRC 规范写的，若实际结构不同会静默回退整行 LRC。
2. `MusicApi.detail` —— 信息流歌曲卡用 `music.trackInfo.UnifiedGetTrackInfo / GetTrackInfo`，参数结构未实测。
3. `AlbumApi.newAlbums` / `SingerApi.list` —— 响应字段名用了多种 fallback（albums/album_list/v_album、singerlist/singer_list），实际返回若都不匹配会显示空。
4. `PlaylistApi.mine` —— 「我喜欢」的 dirId 是否真为 201 取决于 GetPlaylistByUin 的返回，若不同则收藏列表取不到歌。
5. `RecommendApi.homeFeed` —— 只保留 type=200/500 两类卡片，若上游改类型码会整段空。

## 端到端验证清单（真机）

1. 三种登录方式各走一遍，杀掉进程重开仍是登录态。
2. 首页每个板块都有内容、都能点进去、列表能播放。
3. 一首无损歌：能播放（首次等待解密）、下载后能播、标签/封面/歌词写入正确。
4. 歌词页：有逐字歌词的歌显示逐字染色；没有的显示整行。
5. 下载目录选到 SD 卡/其他目录后重启仍可写。
