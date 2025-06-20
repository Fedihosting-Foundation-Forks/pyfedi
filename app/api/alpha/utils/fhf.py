from http import HTTPStatus

import jwt
from flask import current_app, request, jsonify
from flask.wrappers import Response
from sqlalchemy import text

from app import db
from app.email import send_registration_approved_email
from app.models import User, UserRegistration, utcnow, Notification, ModLog
from app.utils import finalize_user_setup, add_to_modlog, get_setting


def fhf_validate_api_auth_admin(permission: str) -> int | None:
    auth = request.headers.get("authorization")
    if auth is None or not auth.startswith("Bearer "):
        return False

    decoded = jwt.decode(
        auth[7:], current_app.config["SECRET_KEY"], algorithms=["HS256"]
    )
    if not decoded:
        return False

    user_id = decoded["sub"]
    issued_at = decoded["iat"]

    sql = """
    select extract(epoch from password_updated_at) as password_updated_at
    from "user" u
    join user_role ur on u.id = ur.user_id
    join role_permission rp on ur.role_id = rp.role_id and rp.permission = :permission
    where u.deleted = false
    and u.banned = false
    and u.id = :user_id
    """

    row = db.session.execute(
        text(sql),
        {
            "permission": permission,
            "user_id": user_id,
        },
    ).scalar_one()

    # backwards compatibility with tokens issued before password_updated_at was populated
    if row is None or issued_at >= row:
        return user_id

    return None


def fhf_list_registration_applications() -> tuple["Response", int]:
    if fhf_validate_api_auth_admin("approve registrations") is None:
        return jsonify({"error": "incorrect_login"}), HTTPStatus.UNAUTHORIZED

    sql = """
    select
        user_registration.user_id,
        user_registration.answer,
        "user".user_name,
        "user".email,
        "user".ip_address
    from user_registration
    join "user" on user_registration.user_id = "user".id
    where user_registration.status = 0
    and "user".verified = true
    and "user".private_key is null -- safety net
    order by created_at desc
    """

    rows = db.session.execute(text(sql)).all()

    return jsonify(
        {
            "applications": [
                {
                    "user_id": row[0],
                    "answer": row[1],
                    "user_name": row[2],
                    "email": row[3],
                    "ip": row[4],
                }
                for row in rows
            ]
        }
    ), HTTPStatus.OK


def fhf_approve_registration_application() -> tuple["Response", int]:
    if (admin_user := fhf_validate_api_auth_admin("approve registrations")) is None:
        return jsonify({"error": "incorrect_login"}), HTTPStatus.UNAUTHORIZED

    body = request.get_json(cache=False)

    if not isinstance(approve := body.get("approve"), bool) or not isinstance(
        user_id := body.get("user_id"), int
    ):
        return jsonify({"error": "invalid_body"}), HTTPStatus.BAD_REQUEST

    user = User.query.get(user_id)
    if user is None:
        return jsonify({"error": "invalid_user_id"}), HTTPStatus.BAD_REQUEST

    if not user.verified:
        return jsonify({"error": "email_not_verified"}), HTTPStatus.BAD_REQUEST

    if user.private_key is not None:
        return jsonify({"error": "private_key_already_present"}), HTTPStatus.BAD_REQUEST

    registration = UserRegistration.query.filter_by(status=0, user_id=user_id).first()
    if not registration:
        return jsonify({"error": "application_not_pending"}), HTTPStatus.BAD_REQUEST

    if approve:
        registration.status = 1
        registration.approved_at = utcnow()
        registration.approved_by = admin_user
        finalize_user_setup(user)
        db.session.commit()
        send_registration_approved_email(user)
        return jsonify(), HTTPStatus.NO_CONTENT

    # fully delete users that aren't approved
    db.session.query(UserRegistration).filter(
        UserRegistration.user_id == user.id
    ).delete()
    db.session.query(Notification).filter(Notification.author_id == user.id).delete()
    db.session.delete(user)
    db.session.commit()
    return jsonify(), HTTPStatus.NO_CONTENT


def fhf_ban_user() -> tuple["Response", int]:
    if (admin_user := fhf_validate_api_auth_admin("ban users")) is None:
        return jsonify({"error": "incorrect_login"}), HTTPStatus.UNAUTHORIZED

    body = request.get_json(cache=False)

    # TODO: implement unban, expiry, content removal

    if (
        not isinstance(ban := body.get("ban"), bool)
        or not isinstance(user_id := body.get("user_id"), int)
        or not ((reason := body.get("reason")) is None or isinstance(reason, str))
    ):
        return jsonify({"error": "invalid_body"}), HTTPStatus.BAD_REQUEST

    if not ban:
        return jsonify({"error": "not_yet_implemented"}), HTTPStatus.BAD_REQUEST

    if user_id == admin_user:
        return jsonify({"error": "cant_ban_self"}), HTTPStatus.BAD_REQUEST

    user = User.query.get(user_id)
    if user is None:
        return jsonify({"error": "invalid_user_id"}), HTTPStatus.BAD_REQUEST

    # TODO: branch to remove content if desired

    user.banned = True
    # can't use add_to_modlog as that doesn't work with api auth
    db.session.add(
        ModLog(
            user_id=admin_user,
            type="admin",
            action="ban_user",
            reason=reason,
            link=user.link(),
            link_text=user.display_name(),
            public=get_setting("public_modlog", False),
        )
    )

    # TODO: maybe consider IP ban?

    db.session.commit()
    return jsonify(), HTTPStatus.NO_CONTENT
