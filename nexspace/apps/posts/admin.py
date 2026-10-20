from django.contrib import admin

from .models import Attachment, Comment, Post


class AttachmentInline(admin.TabularInline):
    model = Attachment
    extra = 0
    readonly_fields = ("kind", "original_name", "size", "content_type")


@admin.register(Post)
class PostAdmin(admin.ModelAdmin):
    list_display = ("id", "kind", "author", "is_anonymous", "is_official", "score", "comment_count", "is_deleted", "created_at")
    list_filter = ("kind", "is_anonymous", "is_official", "is_deleted", "department")
    search_fields = ("body", "title", "author__username")
    raw_id_fields = ("author", "accepted_comment")
    inlines = [AttachmentInline]


@admin.register(Comment)
class CommentAdmin(admin.ModelAdmin):
    list_display = ("id", "post", "author", "depth", "score", "is_deleted", "created_at")
    list_filter = ("is_deleted",)
    raw_id_fields = ("post", "author", "parent", "reply_to")
