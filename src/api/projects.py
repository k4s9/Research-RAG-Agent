from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Project
from src.db.postgres import get_db
from src.schemas.project import ProjectCreateRequest, ProjectListResponse, ProjectResponse

router = APIRouter()


def _response(project: Project) -> ProjectResponse:
    return ProjectResponse(
        id=project.id,
        name=project.name,
        description=project.description,
        created_at=project.created_at.isoformat(),
        updated_at=project.updated_at.isoformat(),
    )


@router.post("", response_model=ProjectResponse, status_code=201)
async def create_project(
    request: ProjectCreateRequest,
    session: AsyncSession = Depends(get_db),
) -> ProjectResponse:
    project = Project(name=request.name.strip(), description=request.description)
    session.add(project)
    await session.commit()
    await session.refresh(project)
    return _response(project)


@router.get("", response_model=ProjectListResponse)
async def get_projects(session: AsyncSession = Depends(get_db)) -> ProjectListResponse:
    result = await session.execute(select(Project).order_by(Project.created_at.desc()))
    projects = list(result.scalars().all())
    return ProjectListResponse(
        projects=[_response(project) for project in projects], total=len(projects)
    )
