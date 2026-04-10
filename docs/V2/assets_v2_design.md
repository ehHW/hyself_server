# Assets V2 设计方案

## 1. 文档目标

本文件定义 V2 的统一资产域设计。

V1 中资源中心、头像上传、回收站恢复已经形成了一套可靠的文件能力，但它仍以“业务内文件记录”为主。V2 需要把它升级为平台级资产能力。

## 2. 设计目标

资产域要解决以下问题：

1. 同一个物理文件如何被多个业务对象复用
2. 资源中心文件与聊天文件消息如何共享底层能力
3. 头像、图片消息、文件消息如何使用统一上传链路
4. 回收站、去重、预览、访问权限如何统一治理

## 3. 核心概念

V2 引入两个核心实体：

1. Asset
2. AssetReference

### 3.1 Asset

Asset 表示一个“物理文件实体”。

它描述：

1. 文件哈希
2. 存储位置
3. MIME 类型
4. 文件大小
5. 原始文件名
6. 媒体类型
7. 存储后端

一个物理文件只对应一个 Asset。

### 3.2 AssetReference

AssetReference 表示“业务对象对 Asset 的引用关系”。

它描述：

1. 谁引用了该 Asset
2. 引用类型是什么
3. 显示名是什么
4. 是否已删除
5. 是否进回收站
6. 业务上的父级目录或容器是谁

一个 Asset 可以被多个 AssetReference 引用。

## 4. 为什么需要两层模型

如果只有 UploadedFile 这种业务记录，会有几个问题：

1. 同一文件被聊天消息和资源中心文件重复建模
2. 回收站恢复和聊天附件复用难统一
3. 对象存储迁移时，物理文件与业务关系难拆开

拆成两层后：

1. Asset 管物理事实
2. AssetReference 管业务关系

## 5. 建议数据模型

### 5.1 Asset

建议字段：

1. id
2. file_md5
3. sha256
4. storage_backend
5. storage_key
6. mime_type
7. media_type
8. file_size
9. original_name
10. extension
11. width
12. height
13. duration_seconds
14. created_by
15. created_at

### 5.2 AssetReference

建议字段：

1. id
2. asset_id
3. owner_user_id
4. ref_domain
5. ref_type
6. ref_object_id
7. display_name
8. parent_reference_id
9. relative_path_cache
10. status
11. recycled_at
12. deleted_at
13. visibility
14. extra_metadata
15. created_at
16. updated_at

## 6. 与现有 UploadedFile 的关系

V2 第一阶段不建议立刻删掉 UploadedFile。

推荐路线：

1. UploadedFile 继续保留
2. 新增 Asset / AssetReference
3. 新上传的文件优先走新模型
4. UploadedFile 逐步变成 AssetReference 的资源中心特化视图或兼容层

当前实现补充：

1. 聊天附件发送已经改为通过 application service 创建 chat 类型的 `AssetReference`
2. 用户头像引用更新与资源中心引用同步也已复用同一批 asset reference service
3. `UploadedFile` 目前仍然承担兼容桥接职责，但新的引用创建逻辑已经开始从业务散点收口

这样可以避免一次性高风险迁移。

## 7. 媒体分类

资产域建议统一定义 media_type：

1. file
2. image
3. audio
4. video
5. avatar
6. system

这样聊天、资源中心、音乐、视频都能复用统一媒体分类。

## 8. 上传架构

### 8.1 上传流程

统一上传流程建议为：

1. 前端发起上传初始化
2. 后端返回 upload session
3. 小文件走直接上传
4. 大文件走分片上传
5. 合并后生成 Asset
6. 业务层决定是否创建 AssetReference

### 8.2 目标接口

建议未来收敛为：

1. POST /api/assets/upload/init/
2. POST /api/assets/upload/chunk/
3. POST /api/assets/upload/merge/
4. POST /api/assets/references/
5. POST /api/assets/references/{id}/restore/
6. POST /api/assets/references/{id}/delete/

V1 的 upload 接口可以先作为兼容层保留。

## 9. 去重策略

### 9.1 物理去重

V2 继续保留基于 MD5 的物理去重。

规则：

1. 如果 Asset 已存在，则不重复保存物理文件
2. 只新建 AssetReference

### 9.2 业务冲突

在业务层仍然要处理：

1. 同目录同名同内容
2. 同目录同名不同内容
3. 回收站中存在同 MD5 引用

这些规则由 AssetReference 层解决，而不是由 Asset 层解决。

## 10. 回收站模型

V2 中回收站只作用于 AssetReference，不作用于 Asset。

原因：

1. 回收站本质是业务引用被移除，不代表物理文件应该立即删除
2. 一个 Asset 可能还被其它地方引用

物理清理规则：

1. 只有当 Asset 没有任何活动引用时，才允许进入物理清理候选
2. 清理任务按引用状态和保留期统一扫描

## 11. 访问控制

资产域需要支持三层权限：

1. 物理可访问性
2. 业务引用可访问性
3. 预览或下载动作权限

例如：

1. 资源中心文件是否允许访问
2. 聊天附件是否允许当前会话成员访问
3. 管理员巡检时是否允许读取附件

## 12. 预览与派生资源

V2 为图片、视频、音频预留派生资源能力。

建议新增 AssetVariant 概念，支持：

1. 缩略图
2. 预览图
3. 转码文件
4. 波形图
5. 视频封面

V2 第一阶段可以只做字段预留，不急着完整实现。

## 13. 聊天接入方式

聊天文件/图片消息接入资产域时：

1. 上传完成后生成 Asset
2. 创建一条 chat 类型的 AssetReference
3. ChatMessage payload 只引用 asset_reference_id

推荐 payload：

```json
{
  "asset_reference_id": 456,
  "display_name": "演示视频.mp4",
  "media_type": "video"
}
```

## 14. 资源中心接入方式

资源中心在 V2 中本质上是 AssetReference 的目录视图。

能力包括：

1. 创建目录引用
2. 浏览引用树
3. 删除引用
4. 恢复引用
5. 重命名引用
6. 搜索引用

## 15. 头像接入方式

头像上传也不再走特殊路径语义，而是：

1. 上传得到 Asset
2. 创建 avatar 类型的 AssetReference
3. 用户资料只保存当前头像引用 ID 或 URL 快照

## 16. 存储后端抽象

V2 定义 StorageProvider 接口，至少支持：

1. put
2. get
3. delete
4. exists
5. signed_url
6. copy
7. move

默认实现：

1. LocalFileSystemStorageProvider

后续实现：

1. S3CompatibleStorageProvider

## 17. 搜索与索引

资产域至少支持这些检索条件：

1. display_name
2. file_md5
3. media_type
4. extension
5. owner_user_id
6. ref_domain
7. recycled_at

V2 第一阶段仍可由数据库完成。

## 18. 迁移顺序

### Phase 1

先建立 Asset / AssetReference 模型与 repository，不替换现有上传入口。

### Phase 2

让头像上传和新聊天附件先走新模型。

当前状态：已落地第一轮。

### Phase 3

再让资源中心新上传文件走新模型。

当前状态：已落地第一轮，资源中心引用同步已接入统一 service，但完整上传入口仍未完全切换。

### Phase 4

最后逐步处理 UploadedFile 到 AssetReference 的兼容迁移。

当前状态：进行中，兼容桥仍保留。

## 19. 验收标准

资产域 V2 完成的标志：

1. 同一个物理文件可以被多个业务引用
2. 回收站只影响引用，不影响仍被使用的物理文件
3. 聊天文件消息与资源中心文件共享统一底层上传能力
4. 本地磁盘与对象存储之间切换时，业务层不需要大改

按当前代码状态，可确认：

1. 第 1 条的引用复用能力已经在 chat / avatar / resource center 三类场景开始落地
2. 第 3 条目前完成了“统一引用服务”这一层，但还没有完成“统一上传入口”这一层
3. 第 4 条仍以设计约束为主，存储 provider 抽象尚未成为当前代码主路径