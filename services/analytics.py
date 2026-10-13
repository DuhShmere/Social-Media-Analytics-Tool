"""Analytics calculations and post filtering.

Everything here works on plain values, model objects, or a query,
and never reads the Flask request directly, so it can be unit
tested without an HTTP request.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from models import Post


def calculate_engagement_rate(interactions, views):
    """Interactions as a percentage of views, rounded to 2 places."""
    if not views:
        return 0

    return round((interactions / views) * 100, 2)


def summarize_posts(posts):
    likes = sum(post.likes or 0 for post in posts)
    comments = sum(post.comments or 0 for post in posts)
    shares = sum(post.shares or 0 for post in posts)
    saves = sum(post.saves or 0 for post in posts)
    total_views = sum(post.views or 0 for post in posts)
    total_interactions = likes + comments + shares + saves

    return {
        "total_posts": len(posts),
        "total_views": total_views,
        "total_interactions": total_interactions,
        "engagement_rate": calculate_engagement_rate(
            total_interactions,
            total_views,
        ),
        "likes": likes,
        "comments": comments,
        "shares": shares,
        "saves": saves,
    }


def calculate_metric_change(current_value, previous_value):
    current_value = current_value or 0
    previous_value = previous_value or 0
    difference = round(current_value - previous_value, 2)

    if previous_value == 0:
        percentage = 0 if difference == 0 else None
    else:
        percentage = round(
            (difference / previous_value) * 100,
            2,
        )

    return {
        "difference": difference,
        "percentage": percentage,
    }


def shorten_title(title, maximum_length=35):
    if len(title) <= maximum_length:
        return title

    return f"{title[:maximum_length - 3]}..."


def format_hour(hour):
    time_value = datetime.strptime(
        str(hour),
        "%H",
    )

    return time_value.strftime(
        "%I:%M %p"
    ).lstrip("0")


def calculate_best_posting_time(
    posts,
    minimum_sample_size=3,
):
    groups = {}

    for post in posts:
        if not post.posted_at:
            continue

        weekday_number = post.posted_at.weekday()
        weekday_name = post.posted_at.strftime("%A")
        window_start = (post.posted_at.hour // 3) * 3

        key = (
            weekday_number,
            weekday_name,
            window_start,
        )

        if key not in groups:
            groups[key] = {
                "posts": 0,
                "views": 0,
                "interactions": 0,
            }

        groups[key]["posts"] += 1
        groups[key]["views"] += post.views or 0
        groups[key]["interactions"] += (
            post.total_interactions
        )

    eligible_groups = []

    for key, values in groups.items():
        if values["posts"] < minimum_sample_size:
            continue

        if values["views"] <= 0:
            continue

        engagement_rate = calculate_engagement_rate(
            values["interactions"],
            values["views"],
        )

        eligible_groups.append(
            {
                "weekday_number": key[0],
                "day": key[1],
                "window_start": key[2],
                "sample_size": values["posts"],
                "engagement_rate": engagement_rate,
            }
        )

    if not eligible_groups:
        largest_group_size = max(
            (
                values["posts"]
                for values in groups.values()
            ),
            default=0,
        )

        return {
            "available": False,
            "minimum_sample_size": minimum_sample_size,
            "largest_group_size": largest_group_size,
        }

    best_group = max(
        eligible_groups,
        key=lambda group: (
            group["engagement_rate"],
            group["sample_size"],
        ),
    )

    start_hour = best_group["window_start"]
    end_hour = start_hour + 2

    return {
        "available": True,
        "day": best_group["day"],
        "time_window": (
            f"{format_hour(start_hour)} – "
            f"{format_hour(end_hour)}"
        ),
        "engagement_rate": best_group["engagement_rate"],
        "sample_size": best_group["sample_size"],
    }


@dataclass(frozen=True)
class PostFilters:
    account_id: int | None = None
    search: str = ""
    platform: str = ""
    content_type: str = ""
    start_date: date | None = None
    end_date: date | None = None

    @classmethod
    def from_args(cls, args):
        """Build filters from query-string arguments.

        Raises ValueError with a user-facing message when a value
        is invalid.
        """
        account_id = None
        account_value = args.get("account_id", "").strip()

        if account_value:
            try:
                account_id = int(account_value)
            except ValueError:
                raise ValueError("Invalid account filter.") from None

        start_date = parse_filter_date(args.get("start_date", ""))
        end_date = parse_filter_date(args.get("end_date", ""))

        if start_date and end_date and start_date > end_date:
            raise ValueError(
                "Start date must be on or before end date."
            )

        return cls(
            account_id=account_id,
            search=args.get("search", "").strip(),
            platform=args.get("platform", "").strip(),
            content_type=args.get("content_type", "").strip(),
            start_date=start_date,
            end_date=end_date,
        )


def parse_filter_date(value):
    value = (value or "").strip()

    if not value:
        return None

    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        raise ValueError(
            "Invalid date filter. Use the YYYY-MM-DD format."
        ) from None


def apply_post_filters(query, filters):
    if filters.account_id is not None:
        query = query.filter(
            Post.social_account_id == filters.account_id
        )

    if filters.search:
        query = query.filter(
            Post.caption.ilike(f"%{filters.search}%")
        )

    if filters.platform:
        query = query.filter(Post.platform == filters.platform)

    if filters.content_type:
        query = query.filter(
            Post.content_type == filters.content_type
        )

    if filters.start_date:
        query = query.filter(
            Post.posted_at >= datetime.combine(
                filters.start_date,
                datetime.min.time(),
            )
        )

    if filters.end_date:
        # Include the whole end day by filtering before the next day.
        query = query.filter(
            Post.posted_at < datetime.combine(
                filters.end_date + timedelta(days=1),
                datetime.min.time(),
            )
        )

    return query
