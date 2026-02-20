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
