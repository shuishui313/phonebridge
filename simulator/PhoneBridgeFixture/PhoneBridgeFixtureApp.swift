import SwiftUI

@main
struct PhoneBridgeFixtureApp: App {
    var body: some Scene {
        WindowGroup {
            FixtureRootView()
        }
    }
}

struct FixtureContact: Identifiable, Hashable {
    let id: String
    let name: String
}

struct FixtureRootView: View {
    var body: some View {
        NavigationStack {
            List {
                Section("合成测试 App") {
                    NavigationLink {
                        FixtureChatListView()
                    } label: {
                        VStack(alignment: .leading, spacing: 4) {
                            Text("WhatsApp（合成）").font(.headline)
                            Text("私信场景：联系人、草稿、发送、回读").font(.caption).foregroundStyle(.secondary)
                        }
                    }
                    .accessibilityIdentifier("app.whatsapp")

                    NavigationLink {
                        FixtureFeedView()
                    } label: {
                        VStack(alignment: .leading, spacing: 4) {
                            Text("X（合成）").font(.headline)
                            Text("信息流场景：撰写、发布、回读").font(.caption).foregroundStyle(.secondary)
                        }
                    }
                    .accessibilityIdentifier("app.x")
                }
            }
            .navigationTitle("PhoneBridge")
        }
    }
}

struct FixtureChatListView: View {
    private let contacts: [FixtureContact] = [
        .init(id: "contact-01", name: "Test Contact"),
        .init(id: "contact-02", name: "Casey"),
        .init(id: "alex-01", name: "Alex"),
        .init(id: "alex-02", name: "Alex")
    ]

    var body: some View {
        List(contacts) { contact in
            NavigationLink(destination: FixtureChatView(contact: contact)) {
                VStack(alignment: .leading) {
                    Text(contact.name).font(.headline)
                    Text(contact.id).font(.caption).foregroundStyle(.secondary)
                }
            }
            .accessibilityIdentifier("contact.\(contact.id)")
        }
        .navigationTitle("WhatsApp（合成）")
    }
}

struct FixtureChatView: View {
    let contact: FixtureContact
    @State private var draft: String = ""
    @State private var messages: [String] = []

    var body: some View {
        VStack(spacing: 12) {
            List(messages.indices, id: \.self) { index in
                Text(messages[index])
                    .accessibilityIdentifier("chat.message.\(index)")
            }

            HStack {
                TextField("Message", text: $draft)
                    .textFieldStyle(.roundedBorder)
                    .accessibilityIdentifier("chat.input")
                Button("Send") {
                    guard !draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return }
                    messages.append(draft)
                    draft = ""
                }
                .accessibilityIdentifier("chat.send")
            }
            .padding(12)
        }
        .navigationTitle(contact.name)
    }
}

struct FixtureFeedView: View {
    @State private var draft: String = ""
    @State private var posts: [String] = []

    var body: some View {
        VStack(spacing: 12) {
            List(posts.indices, id: \.self) { index in
                Text(posts[index])
                    .accessibilityIdentifier("timeline.post.\(index)")
            }

            HStack {
                TextField("What is happening?", text: $draft)
                    .textFieldStyle(.roundedBorder)
                    .accessibilityIdentifier("compose.input")
                Button("Post") {
                    guard !draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return }
                    posts.insert(draft, at: 0)
                    draft = ""
                }
                .accessibilityIdentifier("compose.post")
            }
            .padding(12)
        }
        .navigationTitle("X（合成）")
    }
}
