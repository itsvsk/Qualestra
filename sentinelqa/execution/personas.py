from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class PersonaLoginAdapter:
    persona_id: str
    username: str
    password_secret_name: str

    async def login(self, page: Any, secret_resolver: Any) -> None:
        password = await secret_resolver(self.password_secret_name)
        await page.get_by_label("Email", exact=True).fill(self.username)
        await page.get_by_label("Password", exact=True).fill(password)
        await page.get_by_role("button", name="Sign in", exact=True).click()
        del password
