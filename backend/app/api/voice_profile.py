from fastapi import APIRouter, HTTPException
from app.services.samples import dataset_status
from app.services.personal_model import model_status, start_training

router = APIRouter()


@router.get("/{user_id}/exists")
def exists(user_id: str):
    try:
        return {"exists": bool(dataset_status(user_id)["samples"])}
    except ValueError as error:
        raise HTTPException(400, str(error))


@router.get("/{user_id}")
def get_profile(user_id: str, reference: str = "talk"):
    try:
        dataset = dataset_status(user_id)
        return {"exists": bool(dataset["samples"]), **dataset, "model": model_status(user_id)}
    except ValueError as error:
        raise HTTPException(404, str(error))


@router.get("/{user_id}/audio/{kind}")
def profile_audio(user_id: str, kind: str):
    raise HTTPException(410, "El registro anterior fue reemplazado. Usa las tomas del perfil bilingüe.")


@router.post("/{user_id}/train")
def prepare_existing(user_id: str):
    try:
        return start_training(user_id)
    except ValueError as error:
        raise HTTPException(409, str(error))


@router.post("/")
def create_voice_profile():
    raise HTTPException(410, "La captura anterior esta retirada. Guarda las tomas bilingües en /api/studio/{user}/samples.")
