const bodyApiBase = (document.body?.dataset.apiBase || '').trim().replace(/\/+$/, '');

function apiUrl(path) {
    return bodyApiBase ? `${bodyApiBase}${path}` : path;
}

function setText(selector, value) {
    const element = document.querySelector(selector);
    if (element && typeof value === 'string' && value.trim()) {
        element.textContent = value;
    }
}

async function renderBackendStatus() {
    const statusElement = document.querySelector('[data-api-status]');
    if (!statusElement) {
        return;
    }

    statusElement.textContent = 'Backend: checking...';

    try {
        const response = await fetch(apiUrl('/api/healthz'), {
            method: 'GET',
            headers: { Accept: 'application/json' },
        });

        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }

        const payload = await response.json();
        const service = payload.service || 'backend';
        statusElement.textContent = `Backend: connected (${service})`;
        statusElement.classList.add('is-ok');
        statusElement.classList.remove('is-error');
    } catch (_error) {
        statusElement.textContent = 'Backend: connection failed';
        statusElement.classList.add('is-error');
        statusElement.classList.remove('is-ok');
    }
}

async function hydratePortfolioFromApi() {
    if (!document.querySelector('[data-portfolio-from-api]')) {
        return;
    }

    try {
        const response = await fetch(apiUrl('/api/portfolio'), {
            method: 'GET',
            headers: { Accept: 'application/json' },
        });

        if (!response.ok) {
            return;
        }

        const payload = await response.json();
        const profile = payload.profile || {};

        setText('[data-profile-name]', profile.name);
        setText('[data-profile-intro]', profile.intro);
        setText('[data-about-name]', profile.name);
        setText('[data-about-age]', String(profile.age || ''));
        setText('[data-about-education]', profile.education);
        setText('[data-about-certificates]', profile.certificates);
        setText('[data-contact-email]', profile.email);
        setText('[data-contact-phone]', profile.phone);
        setText('[data-contact-github]', profile.github);
        setText('[data-contact-location]', profile.location);

        if (Array.isArray(payload.skills)) {
            const skillsList = document.querySelector('[data-skills-list]');
            if (skillsList) {
                skillsList.innerHTML = '';
                payload.skills.forEach((skill) => {
                    const li = document.createElement('li');
                    li.textContent = skill;
                    skillsList.appendChild(li);
                });
            }
        }

        if (Array.isArray(payload.projects)) {
            payload.projects.slice(0, 3).forEach((project, index) => {
                setText(`[data-project-title="${index}"]`, project.title || '');
                setText(`[data-project-summary="${index}"]`, project.summary || '');
            });
        }
    } catch (_error) {
        // Keep default static content when API loading fails.
    }
}

document.addEventListener('click', (event) => {
    const target = event.target;
    if (!(target instanceof HTMLElement)) {
        return;
    }

    if (target.matches('[data-confirm]')) {
        const message = target.getAttribute('data-confirm') || '진행할까요?';
        if (!window.confirm(message)) {
            event.preventDefault();
        }
    }
});

document.querySelectorAll('a[href^="#"]').forEach((anchor) => {
    anchor.addEventListener('click', (event) => {
        const href = anchor.getAttribute('href');
        if (!href || href === '#') {
            return;
        }

        const destination = document.querySelector(href);
        if (!destination) {
            return;
        }

        event.preventDefault();
        destination.scrollIntoView({
            behavior: 'smooth',
            block: 'start',
        });
    });
});

void renderBackendStatus();
void hydratePortfolioFromApi();
