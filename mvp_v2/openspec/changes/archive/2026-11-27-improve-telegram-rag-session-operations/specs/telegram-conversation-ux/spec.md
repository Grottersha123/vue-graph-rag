# Spec Delta

## Purpose

Определяет предсказуемое поведение Telegram-диалога при выборе профиля, feedback, пропуске сообщения и возобновлении неактивной сессии без молчаливого удаления пользовательского контекста.

## ADDED Requirements

### Requirement: Profile selection has a completed state

Telegram SHALL show a clear confirmation after profile selection and SHALL remove or disable the profile-selection buttons after successful selection. Repeated callbacks MUST NOT apply the selection twice.

#### Scenario: Profile selected successfully
- **WHEN** разрешённый пользователь выбирает профиль
- **THEN** бот подтверждает выбранный профиль, удаляет или отключает клавиатуру и показывает следующий шаг

### Requirement: Skip feedback is visible and state-safe

The skip action SHALL acknowledge the callback, clear the pending feedback state and send a visible continuation message. It MUST NOT silently delete the user-visible message.

#### Scenario: User skips a feedback comment
- **WHEN** пользователь нажимает «Пропустить»
- **THEN** pending feedback очищается, callback получает подтверждение, а пользователь видит сообщение с дальнейшим вопросом или подсказкой

### Requirement: Inactive sessions receive a greeting

The bot SHALL send a short greeting and current interaction hint when a user starts a chat or sends the first message after the configured inactivity period.

#### Scenario: User returns after inactivity
- **WHEN** с последнего принятого сообщения прошло больше настроенного TTL
- **THEN** бот сначала отправляет приветствие/подсказку и продолжает обработку нового сообщения без потери вопроса
