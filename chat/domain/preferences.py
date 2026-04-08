from user.models import UserPreference


def get_or_create_user_preference(user) -> UserPreference:
    preference, _ = UserPreference.objects.get_or_create(user=user)
    return preference