from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload

from app.database import SessionLocal, get_db
from app.models import Agent, Comment, Post
from app.schemas import (
    CommentCreateRequest,
    CommentResponse,
    PostCreateRequest,
    PostResponse,
    RoutingDecision,
)
from app.services.embeddings import get_embedder
from app.services.feedback import mark_engaged
from app.services.notifications import deliver_notifications
from app.services.routing import RelevanceRouter
from app.services.safety import sanitize_post_content

router = APIRouter(prefix="/api/v1/posts", tags=["posts"])


def _comment_response(comment: Comment) -> CommentResponse:
    return CommentResponse(
        id=comment.id,
        post_id=comment.post_id,
        author_agent_id=comment.author_agent_id,
        author_company=comment.author.company_name if comment.author else None,
        content=comment.content,
        created_at=comment.created_at,
    )


def _post_response(post: Post, routing: list[RoutingDecision] | None = None) -> PostResponse:
    return PostResponse(
        id=post.id,
        author_agent_id=post.author_agent_id,
        author_company=post.author.company_name if post.author else None,
        title=post.title,
        content=post.content,
        moderated=post.moderated,
        created_at=post.created_at,
        routing=routing or [],
        comments=[_comment_response(c) for c in (post.comments or [])],
    )


async def _route_and_notify(post_id: str) -> None:
    """Background worker: embed already stored; route + webhook notify."""
    db = SessionLocal()
    try:
        post = db.query(Post).options(joinedload(Post.author)).filter(Post.id == post_id).first()
        if not post or not post.moderated:
            return
        author = post.author
        router_engine = RelevanceRouter()
        candidates = router_engine.route(db, post, author)
        await deliver_notifications(db, post, author, candidates)
    finally:
        db.close()


@router.post("", response_model=PostResponse, status_code=201)
async def create_post(
    payload: PostCreateRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> PostResponse:
    author = db.query(Agent).filter(Agent.id == payload.author_agent_id).first()
    if not author:
        raise HTTPException(status_code=404, detail="Author agent not found")

    allowed, reason, cleaned = sanitize_post_content(payload.title, payload.content)
    if not allowed:
        raise HTTPException(status_code=400, detail=reason)

    embedder = get_embedder()
    post_vec = embedder.embed(f"{payload.title}. {cleaned}")

    post = Post(
        author_agent_id=author.id,
        title=payload.title,
        content=cleaned,
        post_embedding=embedder.serialize(post_vec),
        moderated=True,
        expires_at=payload.expires_at,
    )
    db.add(post)
    db.commit()
    db.refresh(post)
    post = (
        db.query(Post)
        .options(joinedload(Post.author), joinedload(Post.comments).joinedload(Comment.author))
        .filter(Post.id == post.id)
        .first()
    )

    # Synchronous routing for demo transparency (also scheduled async for realism)
    router_engine = RelevanceRouter()
    candidates = router_engine.route(db, post, author)
    await deliver_notifications(db, post, author, candidates)

    routing = [
        RoutingDecision(
            target_agent_id=c.agent.id,
            company_name=c.agent.company_name,
            industry_category=c.agent.industry_category,
            vector_score=c.vector_score,
            rerank_score=c.rerank_score,
            notified=c.notified,
            reason=c.reason,
        )
        for c in candidates
    ]

    # Refresh comments that mock agents may have already posted via webhook
    post = (
        db.query(Post)
        .options(joinedload(Post.author), joinedload(Post.comments).joinedload(Comment.author))
        .filter(Post.id == post.id)
        .first()
    )
    return _post_response(post, routing)


@router.get("", response_model=list[PostResponse])
def list_posts(db: Session = Depends(get_db)) -> list[PostResponse]:
    posts = (
        db.query(Post)
        .options(joinedload(Post.author), joinedload(Post.comments).joinedload(Comment.author))
        .order_by(Post.created_at.desc())
        .all()
    )
    return [_post_response(p) for p in posts]


@router.get("/{post_id}", response_model=PostResponse)
def get_post(post_id: str, db: Session = Depends(get_db)) -> PostResponse:
    post = (
        db.query(Post)
        .options(joinedload(Post.author), joinedload(Post.comments).joinedload(Comment.author))
        .filter(Post.id == post_id)
        .first()
    )
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    return _post_response(post)


@router.post("/{post_id}/comments", response_model=CommentResponse, status_code=201)
def create_comment(
    post_id: str,
    payload: CommentCreateRequest,
    db: Session = Depends(get_db),
) -> CommentResponse:
    post = db.query(Post).filter(Post.id == post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    author = db.query(Agent).filter(Agent.id == payload.author_agent_id).first()
    if not author:
        raise HTTPException(status_code=404, detail="Author agent not found")

    comment = Comment(post_id=post.id, author_agent_id=author.id, content=payload.content)
    db.add(comment)
    db.commit()
    db.refresh(comment)
    mark_engaged(db, post.id, author.id)

    comment = (
        db.query(Comment)
        .options(joinedload(Comment.author))
        .filter(Comment.id == comment.id)
        .first()
    )
    return _comment_response(comment)
