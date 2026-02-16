/* Attachments: file upload, drag-drop, preview */

const Attachments = (() => {
    let files = [];
    const MAX_SIZE = 10 * 1024 * 1024; // 10 MB

    const ALLOWED_EXTENSIONS = new Set([
        'txt', 'md', 'py', 'js', 'ts', 'json', 'yaml', 'yml', 'csv',
        'pdf', 'png', 'jpg', 'jpeg', 'gif', 'webp', 'css',
        'xml', 'log', 'sh', 'bat', 'ps1', 'sql', 'toml', 'ini', 'cfg',
        'java', 'c', 'cpp', 'h', 'hpp', 'rs', 'go', 'rb', 'php',
    ]);

    function init() {
        const attachBtn = document.getElementById('btn-attach');
        const fileInput = document.getElementById('file-input');

        attachBtn.addEventListener('click', () => fileInput.click());
        fileInput.addEventListener('change', (e) => {
            addFiles(Array.from(e.target.files));
            fileInput.value = '';
        });

        // Drag and drop
        const chatMain = document.querySelector('.chat-main');
        let dragCounter = 0;

        chatMain.addEventListener('dragenter', (e) => {
            e.preventDefault();
            dragCounter++;
            chatMain.classList.add('drag-active');
        });

        chatMain.addEventListener('dragleave', (e) => {
            e.preventDefault();
            dragCounter--;
            if (dragCounter === 0) chatMain.classList.remove('drag-active');
        });

        chatMain.addEventListener('dragover', (e) => {
            e.preventDefault();
        });

        chatMain.addEventListener('drop', (e) => {
            e.preventDefault();
            dragCounter = 0;
            chatMain.classList.remove('drag-active');
            if (e.dataTransfer.files.length > 0) {
                addFiles(Array.from(e.dataTransfer.files));
            }
        });
    }

    function addFiles(newFiles) {
        for (const file of newFiles) {
            const ext = file.name.split('.').pop().toLowerCase();
            if (!ALLOWED_EXTENSIONS.has(ext)) {
                alert(`Unsupported file type: .${ext}\n\nSupported types: ${Array.from(ALLOWED_EXTENSIONS).join(', ')}`);
                continue;
            }
            if (file.size > MAX_SIZE) {
                alert(`File too large: ${file.name} (${(file.size / 1024 / 1024).toFixed(1)} MB)\n\nMaximum size: 10 MB`);
                continue;
            }
            files.push(file);
        }
        renderPreviews();
    }

    function renderPreviews() {
        const container = document.getElementById('attachment-previews');
        container.innerHTML = '';
        files.forEach((file, index) => {
            const preview = document.createElement('div');
            preview.className = 'attachment-preview';

            if (file.type.startsWith('image/')) {
                const img = document.createElement('img');
                img.src = URL.createObjectURL(file);
                preview.appendChild(img);
            }

            const nameSpan = document.createElement('span');
            nameSpan.textContent = `${file.name} (${formatSize(file.size)})`;
            preview.appendChild(nameSpan);

            const removeBtn = document.createElement('button');
            removeBtn.className = 'remove-attachment';
            removeBtn.innerHTML = '&times;';
            removeBtn.addEventListener('click', () => {
                files.splice(index, 1);
                renderPreviews();
            });
            preview.appendChild(removeBtn);

            container.appendChild(preview);
        });
    }

    function formatSize(bytes) {
        if (bytes < 1024) return bytes + ' B';
        if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
        return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
    }

    function getFiles() {
        return files;
    }

    function clear() {
        files = [];
        document.getElementById('attachment-previews').innerHTML = '';
    }

    return { init, getFiles, clear, addFiles };
})();
