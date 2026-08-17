"use client";

/**
 * Interface language.
 *
 * Russian is the default and English is one click away. Both dictionaries live
 * here as nested objects and are flattened into dotted keys at module load, so
 * a missing translation is visible immediately (the key itself is rendered)
 * rather than silently falling back to the other language.
 *
 * Russian verbs are kept in the present tense throughout: agents have names but
 * no gender, and the present tense is the one form that does not have to pick.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useSyncExternalStore,
  type ReactNode,
} from "react";

export type Lang = "ru" | "en";

const STORAGE_KEY = "office.lang";

type Tree = { [key: string]: string | Tree };

const RU: Tree = {
  nav: {
    office: "Офис",
    tasks: "Задачи",
    team: "Команда",
    workflows: "Составы команд",
    keys: "Ключи провайдеров",
    billing: "Оплата",
    signOut: "Выйти",
    advanced: "Для продвинутых",
    credits: "на счету",
    language: "Язык",
  },
  common: {
    cancel: "Отмена",
    save: "Сохранить",
    saved: "Сохранено",
    saveChanges: "Сохранить изменения",
    close: "Закрыть",
    start: "Начать",
    loading: "Загружаем…",
    details: "Подробности",
    hideDetails: "Скрыть подробности",
    error: "Ошибка",
    add: "Добавить",
    edit: "Изменить",
    remove: "Удалить",
    none: "—",
    step: "шаг",
    of: "из",
  },
  office: {
    title: "Офис",
    subtitle: "Ваша команда ИИ-сотрудников. Дайте задачу — и смотрите, как они её делают.",
    giveTask: "Дать задачу",
    taskLabel: "Что нужно сделать?",
    taskPlaceholder:
      "Например: составить план запуска нового тарифа и написать текст для лендинга.",
    taskHint: "Опишите задачу целиком — уточнить по ходу команда не сможет.",
    teamLabel: "Кто будет делать",
    startWork: "За работу!",
    starting: "Собираем команду…",
    atWork: "Команда работает",
    idle: "Команда свободна",
    recent: "Последние задачи",
    noRecent: "Пока задач не было. Дайте первую — они уже на местах.",
    noTeams: "Ещё нет ни одной команды",
    noTeamsHint: "Соберите команду из сотрудников, и они смогут браться за задачи.",
    createTeam: "Собрать команду",
    hireFirst: "Сначала наймите сотрудников",
    hireFirstHint:
      "В офисе пока никого нет. Добавьте хотя бы одного сотрудника — например, аналитика или редактора.",
    hire: "Нанять сотрудника",
    watching: "Смотреть",
    ceiling: "Лимит на задачу: {steps} шагов, {cost}",
  },
  run: {
    task: "Задача",
    inProgress: "В работе",
    finished: "Готово",
    cancel: "Остановить",
    cancelling: "Останавливаем…",
    result: "Результат",
    noResult: "Результата пока нет.",
    documents: "Документы",
    noDocuments: "Пока ничего не готово.",
    chronicle: "Что происходит",
    waiting: "Ждём, когда команда возьмётся за дело…",
    streaming: "Смотрим вживую",
    reconnecting: "Связь потерялась, восстанавливаем…",
    replay: "Показать заново",
    replaying: "Проигрываем запись",
    budgetWarning: "Задача уже съела больше 80% отведённых денег.",
    spent: "Потрачено",
    steps: "Шаги",
    time: "Время",
    openDetails: "Цифры и транскрипт",
  },
  feed: {
    start: "Команда взялась за задачу",
    reading: "{agent} читает доску",
    working: "{agent} пишет",
    tool: "{agent}: {detail}",
    artifact: "Готов документ {detail}",
    pinned: "{agent} вешает результат на доску",
    done: "Задача готова",
    failed: "Задача остановилась",
  },
  tools: {
    web_search: "ищет в интернете",
    web_fetch: "открывает страницу",
    read_artifact: "смотрит документ в архиве",
    list_artifacts: "перебирает архив",
    write_artifact: "печатает документ",
    edit_artifact: "правит документ",
    unknown: "работает с инструментом",
  },
  status: {
    queued: "в очереди",
    running: "в работе",
    succeeded: "готово",
    failed: "сбой",
    cancelled: "остановлено",
    budget_exceeded: "кончились деньги",
    timed_out: "не уложились по времени",
  },
  preset: {
    pipeline: "Конвейер",
    supervisor: "Начальник и подчинённые",
    debate: "Спор и судья",
    blackboard: "Планёрка и задачи",
    swarm: "Передают друг другу",
    custom: "Своя схема",
    pipelineHint: "Каждый делает свою часть по очереди и передаёт дальше.",
    supervisorHint: "Руководитель раздаёт поручения и решает, когда всё готово.",
    debateHint: "Двое спорят несколько кругов, третий выносит решение.",
    blackboardHint: "Планировщик разбивает задачу на пункты, команда их разбирает.",
    swarmHint: "Кто взялся, тот и решает, кому передать дальше.",
    customHint: "Схема, которую вы нарисовали сами.",
  },
  team: {
    title: "Сотрудники",
    subtitle: "У каждого своя роль, своя модель и свои инструменты.",
    newAgent: "Нанять сотрудника",
    editAgent: "Карточка сотрудника",
    name: "Имя",
    role: "Должность",
    rolePlaceholder: "редактор",
    roleHint: "Её видят остальные сотрудники — пишите по делу.",
    model: "Модель",
    modelHint: "Показаны только модели, для которых есть ключ.",
    effort: "Усердие",
    effortHint: "Чем выше, тем дольше и дороже.",
    effortLevel: {
      low: "низкое",
      medium: "среднее",
      high: "высокое",
      xhigh: "очень высокое",
      max: "максимальное",
    },
    noModels: "Нет доступных моделей",
    perMtok: "за млн токенов",
    effortUnsupported: "Эта модель не умеет менять глубину рассуждений — настройка не применится.",
    maxTokens: "Предел ответа (токенов)",
    modelCeiling: "Максимум модели: {value}",
    tools: "Инструменты",
    toolsHint: "До {count} обращений к инструментам за ход. Каждое — отдельный платный запрос.",
    toolsDisabled: "На этой установке инструменты отключены.",
    prompt: "Инструкция",
    promptHint: "Напишите, что этот сотрудник должен выдать, а не как ему себя вести.",
    noPrompt: "Инструкции нет.",
    empty: "В офисе пока никого",
    emptyHint:
      "Наймите нескольких сотрудников с разными ролями — например, аналитика, исполнителя и проверяющего.",
    hireFirst: "Нанять первого",
    dismiss: "Уволить",
    dismissConfirm: "Уволить {name}? История прошлых задач сохранится.",
    sideEffect: {
      read_only: "читает рабочие файлы",
      write: "пишет файлы",
      network: "выходит в интернет",
    },
  },
  workflows: {
    title: "Составы команд",
    subtitle: "Кто с кем работает и в каком порядке.",
    newWorkflow: "Новый состав",
    name: "Название",
    description: "Описание",
    noDescription: "Без описания.",
    updated: "Обновлено {when}",
    preset: "Схема работы",
    presetLocked: "Схему работы после создания поменять нельзя.",
    create: "Создать и настроить",
    createFailed: "Не получилось создать состав.",
    needTwoAgents:
      "Почти всем схемам нужны хотя бы двое. Состав можно создать сейчас, а людей добавить позже.",
    moveUp: "Выше",
    removeNode: "Удалить узел",
    removeEdge: "Удалить стрелку",
    order: "Порядок работы",
    addStep: "Добавить шаг",
    supervisor: "Руководитель",
    workers: "Исполнители",
    maxRounds: "Сколько раз может поручать",
    maxRoundsHint: "Сколько раз руководитель может раздать поручение, прежде чем обязан ответить сам.",
    debaters: "Спорщики",
    judge: "Судья",
    rounds: "Кругов спора",
    roundsHint: "Стоимость примерно: спорщики × круги + 1 шаг.",
    planner: "Планировщик",
    maxTasks: "Максимум пунктов плана",
    entry: "Кто начинает",
    roster: "Состав",
    maxHops: "Максимум передач",
    nodes: "Узлы",
    nodesHint: "Узел-развилка выбирает, по какой стрелке пойти дальше.",
    addAgent: "Добавить узел с сотрудником",
    addRouter: "Добавить развилку",
    startNode: "Стартовый узел",
    edges: "Стрелки",
    addEdge: "Добавить стрелку",
    unassigned: "(не назначен)",
    limits: "Лимиты на задачу",
    limitsHint:
      "Жёсткие пределы для каждой задачи этой команды. Платформенный максимум действует сверху, так что этим можно только понизить планку.",
    maxSteps: "Максимум шагов",
    maxCost: "Максимум денег (в центах)",
    empty: "Команд пока нет",
    emptyHint: "Соберите первый состав — это займёт минуту.",
    diagramHint:
      "На схеме — состав, как он настроен. В некоторых схемах маршрут решается по ходу дела; фактический путь видно в офисе во время работы.",
  },
  tasks: {
    title: "Задачи",
    subtitle: "Всё, что команда делала, с ценой и результатом.",
    newTask: "Дать задачу",
    empty: "Задач пока нет",
    emptyHint: "Откройте офис и дайте команде первое поручение.",
    columnTask: "Задача",
    columnTeam: "Команда",
    columnStatus: "Статус",
    columnSteps: "Шаги",
    columnTokens: "Токены",
    columnCost: "Цена",
    columnStarted: "Начата",
    goToOffice: "В офис",
  },
  keys: {
    title: "Ключи провайдеров",
    subtitle: "Свои ключи к моделям. Хранятся в зашифрованном виде.",
    add: "Добавить ключ",
    addError: "Не получилось добавить ключ.",
    provider: "Провайдер",
    key: "Ключ",
    keyHint: "Ключ шифруется при сохранении и больше нигде не показывается.",
    added: "Добавлен",
    lastUsed: "Использован",
    never: "ни разу",
    active: "работает",
    inactive: "выключен",
    stored: "Сохранённые ключи",
    empty: "Ключей нет",
    emptyHint: "Пока используются ключи платформы.",
    removeConfirm: "Удалить ключ {mask}?",
    removeError: "Не получилось удалить ключ.",
    modeManaged: "Ключи платформы",
    modeByok: "Только свои ключи",
    modeHybrid: "Свои, платформенные — запасные",
    modeManagedHint: "Модели работают на ключах платформы, деньги списываются со счёта.",
    modeByokHint: "Используются только ваши ключи. Модели без ключа недоступны.",
    modeHybridHint: "Сначала пробуем ваш ключ, если его нет — ключ платформы.",
    modeError: "Не получилось сменить режим.",
  },
  billing: {
    title: "Оплата",
    subtitle: "Сколько на счету и куда ушло.",
    balance: "На счету",
    markup: "наценка {percent}%",
    disabled: "Оплата на этой установке отключена.",
    topupLabel: "Пополнить (в центах)",
    topupFailed: "Не получилось пополнить счёт.",
    history: "История",
    empty: "Операций пока нет.",
    date: "Дата",
    kind: "Тип",
    amount: "Сумма",
    balanceAfter: "Остаток",
    comment: "Комментарий",
    members: "Кто имеет доступ",
    addMember: "Дать доступ",
    addMemberHint: "Человек должен быть уже зарегистрирован.",
    addMemberFailed: "Не получилось дать доступ.",
    joined: "с {date}",
    removeMemberConfirm: "Убрать {email} из компании?",
    removeMemberFailed: "Не получилось убрать человека.",
  },
  auth: {
    signIn: "Вход",
    signUp: "Регистрация",
    signUpSubtitle: "Создайте компанию — и наймите первых сотрудников.",
    email: "Почта",
    password: "Пароль",
    passwordHint: "Минимум {min} символов.",
    name: "Имя",
    orgName: "Название компании",
    defaultOrgName: "Моя компания",
    signInAction: "Войти",
    signInFailed: "Не получилось войти.",
    signUpAction: "Зарегистрироваться",
    signUpFailed: "Не получилось зарегистрироваться.",
    toSignUp: "Нет аккаунта? Зарегистрируйтесь",
    toSignIn: "Уже есть аккаунт? Войти",
    tagline: "Команда ИИ-сотрудников, которая работает у вас в офисе.",
  },
};

const EN: Tree = {
  nav: {
    office: "Office",
    tasks: "Tasks",
    team: "Team",
    workflows: "Teams",
    keys: "Provider keys",
    billing: "Billing",
    signOut: "Sign out",
    advanced: "Advanced",
    credits: "credit",
    language: "Language",
  },
  common: {
    cancel: "Cancel",
    save: "Save",
    saved: "Saved",
    saveChanges: "Save changes",
    close: "Close",
    start: "Start",
    loading: "Loading…",
    details: "Details",
    hideDetails: "Hide details",
    error: "Error",
    add: "Add",
    edit: "Edit",
    remove: "Remove",
    none: "—",
    step: "step",
    of: "of",
  },
  office: {
    title: "Office",
    subtitle: "Your team of AI colleagues. Give them a task and watch them do it.",
    giveTask: "Give a task",
    taskLabel: "What needs doing?",
    taskPlaceholder: "For example: plan the launch of a new plan and write the landing page copy.",
    taskHint: "Give the whole brief up front — they cannot ask follow-up questions.",
    teamLabel: "Who does it",
    startWork: "Get to work",
    starting: "Gathering the team…",
    atWork: "The team is working",
    idle: "The team is free",
    recent: "Recent tasks",
    noRecent: "No tasks yet. Give them the first one — they are already at their desks.",
    noTeams: "No teams yet",
    noTeamsHint: "Put a team together and they can start taking on tasks.",
    createTeam: "Put a team together",
    hireFirst: "Hire someone first",
    hireFirstHint:
      "The office is empty. Add at least one colleague — an analyst or an editor, say.",
    hire: "Hire a colleague",
    watching: "Watch",
    ceiling: "Ceiling per task: {steps} steps, {cost}",
  },
  run: {
    task: "Task",
    inProgress: "In progress",
    finished: "Done",
    cancel: "Stop",
    cancelling: "Stopping…",
    result: "Result",
    noResult: "No result yet.",
    documents: "Documents",
    noDocuments: "Nothing ready yet.",
    chronicle: "What is happening",
    waiting: "Waiting for the team to pick it up…",
    streaming: "Watching live",
    reconnecting: "Lost the connection, reconnecting…",
    replay: "Play it back",
    replaying: "Playing the recording",
    budgetWarning: "This task has used more than 80% of its money.",
    spent: "Spent",
    steps: "Steps",
    time: "Time",
    openDetails: "Numbers and transcript",
  },
  feed: {
    start: "The team picked up the task",
    reading: "{agent} is reading the board",
    working: "{agent} is writing",
    tool: "{agent}: {detail}",
    artifact: "Document ready: {detail}",
    pinned: "{agent} pins the result to the board",
    done: "The task is done",
    failed: "The task stopped",
  },
  tools: {
    web_search: "searching the web",
    web_fetch: "opening a page",
    read_artifact: "reading a document",
    list_artifacts: "going through the archive",
    write_artifact: "printing a document",
    edit_artifact: "editing a document",
    unknown: "using a tool",
  },
  status: {
    queued: "queued",
    running: "running",
    succeeded: "done",
    failed: "failed",
    cancelled: "stopped",
    budget_exceeded: "out of money",
    timed_out: "timed out",
  },
  preset: {
    pipeline: "Conveyor",
    supervisor: "Manager and team",
    debate: "Debate and judge",
    blackboard: "Plan and tasks",
    swarm: "Hand-off",
    custom: "Custom",
    pipelineHint: "Each one does their part in turn and passes it on.",
    supervisorHint: "A manager hands out the work and decides when it is done.",
    debateHint: "Two argue for a few rounds, a third rules.",
    blackboardHint: "A planner splits the task into items, the team works through them.",
    swarmHint: "Whoever holds the work decides who gets it next.",
    customHint: "The shape you drew yourself.",
  },
  team: {
    title: "Colleagues",
    subtitle: "Each has a role, a model and their own tools.",
    newAgent: "Hire a colleague",
    editAgent: "Colleague",
    name: "Name",
    role: "Role",
    rolePlaceholder: "reviewer",
    roleHint: "The others see it, so make it descriptive.",
    model: "Model",
    modelHint: "Only models you have a key for are listed.",
    effort: "Effort",
    effortHint: "Higher costs more and takes longer.",
    effortLevel: {
      low: "low",
      medium: "medium",
      high: "high",
      xhigh: "very high",
      max: "maximum",
    },
    noModels: "No models available",
    perMtok: "per Mtok",
    effortUnsupported: "This model has no reasoning-depth control; the setting is ignored.",
    maxTokens: "Max output tokens",
    modelCeiling: "Model ceiling: {value}",
    tools: "Tools",
    toolsHint: "Up to {count} tool round trips per turn. Each one is another billed call.",
    toolsDisabled: "Tools are disabled on this deployment.",
    prompt: "Instructions",
    promptHint: "Say what this colleague should produce, not how to behave in general.",
    noPrompt: "No instructions.",
    empty: "Nobody here yet",
    emptyHint:
      "Hire a few colleagues with distinct roles — an analyst, a builder and a reviewer, say.",
    hireFirst: "Hire the first one",
    dismiss: "Let go",
    dismissConfirm: "Let {name} go? Past tasks keep their history.",
    sideEffect: {
      read_only: "reads working files",
      write: "writes files",
      network: "reaches the internet",
    },
  },
  workflows: {
    title: "Teams",
    subtitle: "Who works with whom, and in what order.",
    newWorkflow: "New team",
    name: "Name",
    description: "Description",
    noDescription: "No description.",
    updated: "Updated {when}",
    preset: "Shape of work",
    presetLocked: "The shape cannot be changed after the team is created.",
    create: "Create and set up",
    createFailed: "Could not create the team.",
    needTwoAgents:
      "Most shapes need at least two colleagues. You can create the team now and add people later.",
    moveUp: "Move up",
    removeNode: "Remove node",
    removeEdge: "Remove edge",
    order: "Order of work",
    addStep: "Add a step",
    supervisor: "Manager",
    workers: "Team",
    maxRounds: "Delegation rounds",
    maxRoundsHint: "How many times the manager may delegate before it has to answer itself.",
    debaters: "Debaters",
    judge: "Judge",
    rounds: "Rounds",
    roundsHint: "Cost is roughly debaters × rounds + 1 steps.",
    planner: "Planner",
    maxTasks: "Max items in the plan",
    entry: "Who starts",
    roster: "Roster",
    maxHops: "Max hand-offs",
    nodes: "Nodes",
    nodesHint: "A router node picks which outgoing edge to follow.",
    addAgent: "Add a colleague node",
    addRouter: "Add a router",
    startNode: "Start node",
    edges: "Edges",
    addEdge: "Add an edge",
    unassigned: "(unassigned)",
    limits: "Ceiling per task",
    limitsHint:
      "Hard limits for every task this team takes on. The platform maximum still applies on top, so these can only lower the ceiling.",
    maxSteps: "Max steps",
    maxCost: "Max cost (cents)",
    empty: "No teams yet",
    emptyHint: "Put the first one together — it takes a minute.",
    diagramHint:
      "The diagram shows the team as configured. In some shapes the route is decided at run time; the office shows the path actually taken.",
  },
  tasks: {
    title: "Tasks",
    subtitle: "Everything the team has done, with what it cost and what it produced.",
    newTask: "Give a task",
    empty: "No tasks yet",
    emptyHint: "Open the office and give the team its first brief.",
    columnTask: "Task",
    columnTeam: "Team",
    columnStatus: "Status",
    columnSteps: "Steps",
    columnTokens: "Tokens",
    columnCost: "Cost",
    columnStarted: "Started",
    goToOffice: "To the office",
  },
  keys: {
    title: "Provider keys",
    subtitle: "Your own model keys. Stored encrypted.",
    add: "Add a key",
    addError: "Could not add the key.",
    provider: "Provider",
    key: "Key",
    keyHint: "The key is encrypted on save and never shown again.",
    added: "Added",
    lastUsed: "Last used",
    never: "never",
    active: "active",
    inactive: "off",
    stored: "Stored keys",
    empty: "No keys",
    emptyHint: "Platform keys are in use.",
    removeConfirm: "Remove key {mask}?",
    removeError: "Could not remove the key.",
    modeManaged: "Platform keys",
    modeByok: "Own keys only",
    modeHybrid: "Own keys, platform as fallback",
    modeManagedHint: "Models run on platform keys and the cost comes off your balance.",
    modeByokHint: "Only your keys are used. Models without a key are unavailable.",
    modeHybridHint: "Your key first; the platform key when you have none.",
    modeError: "Could not change the mode.",
  },
  billing: {
    title: "Billing",
    subtitle: "What is on the account and where it went.",
    balance: "Balance",
    markup: "{percent}% markup",
    disabled: "Billing is disabled on this deployment.",
    topupLabel: "Top up (cents)",
    topupFailed: "Could not top up the account.",
    history: "History",
    empty: "No entries yet.",
    date: "Date",
    kind: "Kind",
    amount: "Amount",
    balanceAfter: "Balance after",
    comment: "Comment",
    members: "Who has access",
    addMember: "Give access",
    addMemberHint: "The person must already have an account.",
    addMemberFailed: "Could not give access.",
    joined: "since {date}",
    removeMemberConfirm: "Remove {email} from the company?",
    removeMemberFailed: "Could not remove the person.",
  },
  auth: {
    signIn: "Sign in",
    signUp: "Sign up",
    signUpSubtitle: "Create a company — then hire your first colleagues.",
    email: "Email",
    password: "Password",
    passwordHint: "At least {min} characters.",
    name: "Name",
    orgName: "Company name",
    defaultOrgName: "My company",
    signInAction: "Sign in",
    signInFailed: "Could not sign in.",
    signUpAction: "Create account",
    signUpFailed: "Could not create the account.",
    toSignUp: "No account? Sign up",
    toSignIn: "Already have an account? Sign in",
    tagline: "A team of AI colleagues working in your office.",
  },
};

function flatten(tree: Tree, prefix = ""): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [key, value] of Object.entries(tree)) {
    const path = prefix ? `${prefix}.${key}` : key;
    if (typeof value === "string") out[path] = value;
    else Object.assign(out, flatten(value, path));
  }
  return out;
}

const DICTIONARIES: Record<Lang, Record<string, string>> = {
  ru: flatten(RU),
  en: flatten(EN),
};

export type Translate = (key: string, vars?: Record<string, string | number>) => string;

interface I18nValue {
  lang: Lang;
  setLang: (lang: Lang) => void;
  t: Translate;
}

const I18nContext = createContext<I18nValue | null>(null);

/**
 * The choice lives outside React, in `localStorage`.
 *
 * Reading it through `useSyncExternalStore` is what keeps hydration honest:
 * the server and the first client render both use `DEFAULT_LANG`, and React
 * then swaps in the stored value without ever comparing two different trees.
 */
const DEFAULT_LANG: Lang = "ru";

let currentLang: Lang | null = null;
const langListeners = new Set<() => void>();

function readLang(): Lang {
  if (currentLang === null) {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    currentLang = stored === "en" || stored === "ru" ? stored : DEFAULT_LANG;
  }
  return currentLang;
}

function serverLang(): Lang {
  return DEFAULT_LANG;
}

/**
 * The current language outside React.
 *
 * Date and number formatting needs the language but has no component to hang a
 * hook on, and threading it through every call site would be noise. Never
 * touches `window`, so it is safe on the server.
 */
export function activeLang(): Lang {
  return currentLang ?? DEFAULT_LANG;
}

function subscribeLang(listener: () => void): () => void {
  langListeners.add(listener);
  return () => {
    langListeners.delete(listener);
  };
}

export function I18nProvider({ children }: { children: ReactNode }) {
  const lang = useSyncExternalStore(subscribeLang, readLang, serverLang);

  useEffect(() => {
    document.documentElement.lang = lang;
  }, [lang]);

  const setLang = useCallback((next: Lang) => {
    currentLang = next;
    window.localStorage.setItem(STORAGE_KEY, next);
    for (const listener of langListeners) listener();
  }, []);

  const t = useCallback<Translate>(
    (key, vars) => {
      const template = DICTIONARIES[lang][key] ?? key;
      if (!vars) return template;
      return template.replace(/\{(\w+)\}/g, (match, name: string) =>
        name in vars ? String(vars[name]) : match,
      );
    },
    [lang],
  );

  const value = useMemo(() => ({ lang, setLang, t }), [lang, setLang, t]);

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n(): I18nValue {
  const context = useContext(I18nContext);
  if (!context) throw new Error("useI18n must be used inside <I18nProvider>");
  return context;
}

export function useT(): Translate {
  return useI18n().t;
}

/** Friendly name for a tool, used in the office feed. */
export function toolLabel(t: Translate, tool: string): string {
  const key = `tools.${tool}`;
  const label = t(key);
  return label === key ? t("tools.unknown") : label;
}
