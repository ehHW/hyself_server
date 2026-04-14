from importlib import import_module


_COMMAND_EXPORTS = {
    "execute_send_asset_message_command": "chat.application.commands.attachments",
    "execute_hide_conversation_command": "chat.application.commands.conversations",
    "execute_toggle_conversation_pin_command": "chat.application.commands.conversations",
    "execute_update_conversation_preference_command": "chat.application.commands.conversations",
    "execute_create_group_conversation_command": "chat.application.commands.create_group_conversation",
    "execute_delete_friend_command": "chat.application.commands.friendships",
    "execute_delete_message_for_user_command": "chat.application.commands.message_visibility",
    "execute_handle_friend_request_command": "chat.application.commands.friendships",
    "execute_submit_friend_request_command": "chat.application.commands.friendships",
    "execute_update_friend_setting_command": "chat.application.commands.friendships",
    "execute_forward_messages_command": "chat.application.commands.forwarding",
    "execute_apply_group_invitation_command": "chat.application.commands.group_management",
    "execute_handle_group_join_request_command": "chat.application.commands.group_management",
    "execute_invite_group_member_command": "chat.application.commands.group_management",
    "execute_leave_group_conversation_command": "chat.application.commands.group_management",
    "execute_mute_group_member_command": "chat.application.commands.group_management",
    "execute_remove_group_member_command": "chat.application.commands.group_management",
    "execute_disband_group_conversation_command": "chat.application.commands.group_management",
    "execute_transfer_group_owner_command": "chat.application.commands.group_management",
    "execute_update_group_config_command": "chat.application.commands.group_management",
    "execute_update_group_member_role_command": "chat.application.commands.group_management",
    "execute_open_direct_conversation_command": "chat.application.commands.open_direct_conversation",
    "execute_mark_conversation_read_command": "chat.application.commands.realtime",
    "execute_send_text_message_command": "chat.application.commands.realtime",
    "execute_restore_revoked_draft_command": "chat.application.commands.revocation",
    "execute_revoke_message_command": "chat.application.commands.revocation",
    "execute_update_chat_settings_command": "chat.application.commands.settings",
}


def __getattr__(name: str):
    module_path = _COMMAND_EXPORTS.get(name)
    if not module_path:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = import_module(module_path)
    value = getattr(module, name)
    globals()[name] = value
    return value

__all__ = [
    "execute_create_group_conversation_command",
    "execute_apply_group_invitation_command",
    "execute_delete_friend_command",
    "execute_delete_message_for_user_command",
    "execute_forward_messages_command",
    "execute_handle_friend_request_command",
    "execute_handle_group_join_request_command",
    "execute_hide_conversation_command",
    "execute_invite_group_member_command",
    "execute_leave_group_conversation_command",
    "execute_disband_group_conversation_command",
    "execute_mark_conversation_read_command",
    "execute_mute_group_member_command",
    "execute_open_direct_conversation_command",
    "execute_remove_group_member_command",
    "execute_transfer_group_owner_command",
    "execute_send_asset_message_command",
    "execute_send_text_message_command",
    "execute_revoke_message_command",
    "execute_restore_revoked_draft_command",
    "execute_submit_friend_request_command",
    "execute_toggle_conversation_pin_command",
    "execute_update_chat_settings_command",
    "execute_update_conversation_preference_command",
    "execute_update_friend_setting_command",
    "execute_update_group_config_command",
    "execute_update_group_member_role_command",
]