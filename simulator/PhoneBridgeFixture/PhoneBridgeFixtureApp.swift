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
    private let contacts: [FixtureContact] = [
        .init(id: "test-01", name: "测试联系人"),
        .init(id: "xiaoming-01", name: "小明"),
        .init(id: "alex-01", name: "Alex"),
        .init(id: "alex-02", name: "Alex")
    ]

    var body: some View {
        NavigationStack {
            List(contacts) { contact in
                NavigationLink(destination: FixtureChatView(contact: contact)) {
                    VStack(alignment: .leading) {
                        Text(contact.name).font(.headline)
                        Text(contact.id).font(.caption).foregroundStyle(.secondary)
                    }
                }
                .accessibilityIdentifier("contact.\(contact.id)")
            }
            .navigationTitle("PhoneBridge")
            .accessibilityIdentifier("fixture.contacts")
        }
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
            .accessibilityIdentifier("chat.history")

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
        .accessibilityIdentifier("chat.\(contact.id)")
    }
}
