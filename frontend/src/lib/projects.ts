export type Project = {
  id: string;
  name: string;
  jobIds: string[];
  createdAt: string;
};

const KEY = "multeagent_projects_v1";

function read(): Project[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as Project[];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function write(projects: Project[]) {
  localStorage.setItem(KEY, JSON.stringify(projects));
}

export function listProjects(): Project[] {
  return read().sort((a, b) => b.createdAt.localeCompare(a.createdAt));
}

export function createProject(name: string): Project {
  const project: Project = {
    id: crypto.randomUUID(),
    name: name.trim() || "Untitled project",
    jobIds: [],
    createdAt: new Date().toISOString(),
  };
  const all = read();
  all.push(project);
  write(all);
  return project;
}

export function renameProject(id: string, name: string): Project[] {
  const all = read().map((p) => (p.id === id ? { ...p, name: name.trim() || p.name } : p));
  write(all);
  return all;
}

export function deleteProject(id: string): Project[] {
  const all = read().filter((p) => p.id !== id);
  write(all);
  return all;
}

export function assignJobToProject(projectId: string, jobId: string): Project[] {
  const all = read().map((p) => {
    if (p.id !== projectId) return p;
    if (p.jobIds.includes(jobId)) return p;
    return { ...p, jobIds: [jobId, ...p.jobIds] };
  });
  write(all);
  return all;
}

export function activeProjectId(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem("multeagent_active_project");
}

export function setActiveProjectId(id: string | null) {
  if (typeof window === "undefined") return;
  if (id) localStorage.setItem("multeagent_active_project", id);
  else localStorage.removeItem("multeagent_active_project");
}
